#!/usr/bin/env python3
"""
Master Archive Export Tool
Exports games and metadata from a master archive (NFS mount) to user destination
Uses symlinks for files when possible to save space
Supports interactive mode for selective game export
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
from difflib import SequenceMatcher
import re


class ArchiveExporter:
    """Main class for exporting from master archive to user destination"""
    
    # Master archive base path
    DEFAULT_ARCHIVE_PATH = "/mnt/Emulators/Master Archive"
    
    # Path to formats configuration file
    FORMATS_CONFIG_FILE = "fe_formats.json"
    
    def __init__(self, source_path: str, destination_path: Optional[str], 
                 dest_format: str = 'es-de', config_path: Optional[str] = None,
                 dry_run: bool = False, verbose: bool = False, use_symlinks: bool = True):
        """
        Initialize the archive exporter
        
        Args:
            source_path: Path to master archive (NFS mount)
            destination_path: User's destination directory (None to use format default)
            dest_format: Destination format (default: 'es-de')
            config_path: Optional path to configuration file
            dry_run: If True, don't create any symlinks/copies (simulation mode)
            verbose: Enable verbose logging
            use_symlinks: If True, create symlinks; if False, copy files (default: True)
        """
        self.source = Path(source_path)
        self.dest_format = dest_format.lower()
        self.config_path = config_path
        self.config = {}
        self.dry_run = dry_run
        self.verbose = verbose
        self.use_symlinks = use_symlinks
        self.logger = self._setup_logging()
        
        # Load supported formats from JSON file
        self.supported_formats = self._load_formats_config()
        
        # Validate destination format
        if self.dest_format not in self.supported_formats:
            raise ValueError(
                f"Unsupported destination format: {self.dest_format}. "
                f"Supported formats: {', '.join(self.supported_formats.keys())}"
            )
        
        self.format_config = self.supported_formats[self.dest_format]
        
        # Load platform mappings
        self.platform_mappings = self.format_config.get('platform_mappings', {})
        self.custom_systems_path = self.format_config.get('custom_systems_path')
        
        # Determine destination - use override if provided, otherwise format default
        if destination_path:
            self.destination = Path(destination_path).expanduser()
            self.logger.info(f"Using custom destination: {self.destination}")
        else:
            default_dest = self.format_config['default_destination']
            self.destination = Path(default_dest).expanduser()
            self.logger.info(f"Using default destination for {self.dest_format}: {self.destination}")
        
        self.logger.info(f"Destination format: {self.format_config['name']}")
        
        # Cache for scanned data
        self.available_platforms = {}
        self.platform_games = {}
        self.unmapped_platforms = []  # Track platforms without mappings
        self.auto_select_metadata = False  # Flag for auto-selecting first metadata file
        
        # Validate paths
        self._validate_paths()
        
        # Load configuration if provided
        if config_path:
            self._load_config()
    
    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration"""
        log_level = logging.DEBUG if self.verbose else logging.INFO
        
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('archive_export.log')
            ]
        )
        logger = logging.getLogger('ArchiveExporter')
        logger.setLevel(log_level)
        
        if self.dry_run:
            logger.info("DRY RUN MODE - No files will be created")
        
        if not self.use_symlinks:
            logger.info("COPY MODE - Files will be copied instead of symlinked")
        
        return logger
    
    def _load_formats_config(self) -> Dict:
        """
        Load supported formats configuration from JSON file
        
        Returns:
            Dictionary of supported formats
        """
        try:
            # Look for config file in same directory as script
            script_dir = Path(__file__).parent
            config_file = script_dir / self.FORMATS_CONFIG_FILE
            
            if not config_file.exists():
                # Fallback to current directory
                config_file = Path(self.FORMATS_CONFIG_FILE)
            
            if not config_file.exists():
                raise FileNotFoundError(
                    f"Formats configuration file not found: {self.FORMATS_CONFIG_FILE}"
                )
            
            with open(config_file, 'r') as f:
                config_data = json.load(f)
            
            if 'formats' not in config_data:
                raise ValueError(
                    f"Invalid formats configuration file: missing 'formats' key"
                )
            
            formats = config_data['formats']
            self.logger.debug(f"Loaded {len(formats)} format(s) from {config_file}")
            
            # Validate format configurations
            self._validate_format_configs(formats)
            
            return formats
            
        except FileNotFoundError as e:
            self.logger.error(f"Error loading formats config: {e}")
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing formats config JSON: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error loading formats config: {e}")
            raise
    
    def _validate_format_configs(self, formats: Dict):
        """
        Validate that all format configurations have required fields
        
        Args:
            formats: Dictionary of format configurations
        """
        required_fields = ['name', 'default_destination', 'description']
        
        for format_id, format_config in formats.items():
            # Skip documentation entries
            if format_id == '_documentation':
                continue
            
            # Check required fields
            missing_fields = [field for field in required_fields if field not in format_config]
            if missing_fields:
                raise ValueError(
                    f"Format '{format_id}' is missing required fields: {', '.join(missing_fields)}\n"
                    f"Required fields: {', '.join(required_fields)}"
                )
            
            # Validate default_destination is not empty
            if not format_config['default_destination']:
                raise ValueError(
                    f"Format '{format_id}' has empty 'default_destination' field"
                )
            
            self.logger.debug(f"Validated format configuration: {format_id}")
    
    def map_platform_name(self, archive_platform_name: str) -> Optional[str]:
        """
        Map master archive platform name to destination format platform name
        
        Args:
            archive_platform_name: Platform name from master archive
            
        Returns:
            Mapped platform name for destination format, or None if unmapped
        """
        mapped_name = self.platform_mappings.get(archive_platform_name)
        
        if not mapped_name:
            # Check if it exists as a custom system before warning
            if self.dest_format == 'es-de':
                existing_system = self.check_existing_custom_system(archive_platform_name)
                if existing_system:
                    # Platform exists as custom system, no warning needed
                    return existing_system
            
            # Only warn if it's not a custom system
            self.logger.warning(f"No platform mapping found for: {archive_platform_name}")
            if archive_platform_name not in self.unmapped_platforms:
                self.unmapped_platforms.append(archive_platform_name)
        
        return mapped_name
    
    def check_existing_custom_system(self, archive_platform_name: str) -> Optional[str]:
        """
        Check if a custom system already exists for this platform in ES-DE custom systems XML
        
        Args:
            archive_platform_name: Platform name from master archive
            
        Returns:
            System name if found, None otherwise
        """
        if not self.custom_systems_path:
            return None
        
        custom_systems_file = Path(self.custom_systems_path).expanduser()
        
        if not custom_systems_file.exists():
            return None
        
        try:
            import xml.etree.ElementTree as ET
            
            tree = ET.parse(custom_systems_file)
            root = tree.getroot()
            
            # Look through all systems to find one with matching fullname or close match
            for system in root.findall('system'):
                name_elem = system.find('name')
                fullname_elem = system.find('fullname')
                
                if name_elem is not None and fullname_elem is not None:
                    # Check if fullname matches archive platform name
                    if fullname_elem.text == archive_platform_name:
                        system_name = name_elem.text
                        self.logger.info(
                            f"Found existing custom system for '{archive_platform_name}': {system_name}"
                        )
                        # Add to platform mappings for this session
                        self.platform_mappings[archive_platform_name] = system_name
                        return system_name
            
        except Exception as e:
            self.logger.warning(f"Error checking existing custom systems: {e}")
        
        return None
    
    def prompt_add_custom_system(self, archive_platform_name: str) -> Optional[Dict]:
        """
        Prompt user to add a custom system for an unmapped platform
        
        Args:
            archive_platform_name: Platform name from master archive
            
        Returns:
            Dictionary with custom system information, or None if skipped
        """
        print(f"\n{'='*70}")
        print(f"UNMAPPED PLATFORM: {archive_platform_name}")
        print(f"{'='*70}")
        print(f"This platform is not mapped in the {self.dest_format} configuration.")
        print(f"You can add it as a custom system to ES-DE.")
        print()
        
        choice = input("Add as custom system? (y/n): ").strip().lower()
        
        if choice not in ['y', 'yes']:
            print("Skipping platform.")
            return None
        
        # Gather system information
        print("\nEnter system information (press Enter for default):")
        
        # Suggest a system name based on archive name
        default_name = archive_platform_name.lower().replace(' ', '').replace('-', '')
        system_name = input(f"System name [{default_name}]: ").strip() or default_name
        
        full_name = input(f"Full name [{archive_platform_name}]: ").strip() or archive_platform_name
        
        # Suggest common extensions
        print("\nCommon file extensions (comma-separated, e.g., .zip,.7z,.bin)")
        extensions = input("Extensions [.zip,.7z]: ").strip() or ".zip,.7z"
        
        # Suggest a common emulator
        print("\nEmulator setup:")
        print("  Use ES-DE placeholders: %EMULATOR_RETROARCH%, %CORE_RETROARCH%, %ROM%")
        print("  Examples:")
        print("    RetroArch: %EMULATOR_RETROARCH% -L %CORE_RETROARCH%/[core]_libretro.so %ROM%")
        print("    Standalone: /path/to/emulator %ROM%")
        
        emulator_type = input("\nEmulator type (retroarch/standalone) [retroarch]: ").strip().lower() or "retroarch"
        
        if emulator_type == "retroarch":
            core = input("RetroArch core name (e.g., mame, nestopia, snes9x): ").strip()
            if core:
                command = f"%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/{core}_libretro.so %ROM%"
            else:
                # Default generic RetroArch command
                command = "%EMULATOR_RETROARCH% %ROM%"
        else:
            command = input("Full emulator command (use %ROM% for game path): ").strip()
            if not command:
                command = "%EMULATOR_RETROARCH% %ROM%"  # Fallback
        
        return {
            'name': system_name,
            'fullname': full_name,
            'path': f"./roms/{system_name}",
            'extensions': extensions,
            'command': command,
            'archive_name': archive_platform_name
        }
    
    def update_es_systems_xml(self, custom_system: Dict) -> bool:
        """
        Update ES-DE custom systems XML with new platform
        
        Args:
            custom_system: Dictionary with system information
            
        Returns:
            True if successful, False otherwise
        """
        if not self.custom_systems_path:
            self.logger.error("No custom systems path configured for this format")
            return False
        
        custom_systems_file = Path(self.custom_systems_path).expanduser()
        
        # Create the directory if it doesn't exist
        custom_systems_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if file exists, create template if not
        if not custom_systems_file.exists():
            self.logger.info(f"Creating new custom systems file: {custom_systems_file}")
            xml_content = '<?xml version="1.0"?>\n<systemList>\n</systemList>\n'
            custom_systems_file.write_text(xml_content)
        
        try:
            import xml.etree.ElementTree as ET
            
            # Parse existing file
            tree = ET.parse(custom_systems_file)
            root = tree.getroot()
            
            # Check if system already exists
            for system in root.findall('system'):
                name_elem = system.find('name')
                if name_elem is not None and name_elem.text == custom_system['name']:
                    self.logger.warning(f"System '{custom_system['name']}' already exists in custom systems")
                    # Add to platform mappings for this session even though we're not adding it
                    self.platform_mappings[custom_system['archive_name']] = custom_system['name']
                    print(f"ℹ System '{custom_system['name']}' already exists, using existing configuration")
                    return True
            
            # Create new system element
            system = ET.SubElement(root, 'system')
            
            name_elem = ET.SubElement(system, 'name')
            name_elem.text = custom_system['name']
            
            fullname_elem = ET.SubElement(system, 'fullname')
            fullname_elem.text = custom_system['fullname']
            
            path_elem = ET.SubElement(system, 'path')
            path_elem.text = custom_system['path']
            
            extensions_elem = ET.SubElement(system, 'extension')
            extensions_elem.text = custom_system['extensions']
            
            command_elem = ET.SubElement(system, 'command')
            command_elem.text = custom_system['command']
            
            platform_elem = ET.SubElement(system, 'platform')
            platform_elem.text = custom_system['name']
            
            theme_elem = ET.SubElement(system, 'theme')
            theme_elem.text = custom_system['name']
            
            # Format XML
            self._indent_xml(root)
            
            # Show preview of what will be added
            system_xml = self._format_system_element(system)
            print(f"\n{'='*70}")
            print("CUSTOM SYSTEM XML TO BE ADDED:")
            print(f"{'='*70}")
            print(system_xml)
            print(f"{'='*70}")
            
            if self.dry_run:
                print(f"\nDRY-RUN: Would add to {custom_systems_file}")
            else:
                print(f"\nTarget file: {custom_systems_file}")
            
            # Write XML (unless dry-run)
            if not self.dry_run:
                tree.write(custom_systems_file, encoding='utf-8', xml_declaration=True)
                self.logger.info(f"Added custom system '{custom_system['name']}' to {custom_systems_file}")
                print(f"\n✓ Successfully added '{custom_system['fullname']}' as custom system")
                print(f"  System directory: {custom_system['path']}")
                print(f"  You may need to restart ES-DE to see the new system")
            else:
                print(f"\n[DRY-RUN] Would add '{custom_system['fullname']}' as custom system")
                print(f"  System directory would be: {custom_system['path']}")
            
            # Add to platform mappings for this session
            self.platform_mappings[custom_system['archive_name']] = custom_system['name']
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating custom systems XML: {e}")
            return False
    
    def _validate_paths(self):
        """Validate source and destination paths, and ensure format-specific directories exist"""
        # Validate source path
        if not self.source.exists():
            raise ValueError(f"Source path does not exist: {self.source}")
        
        if not self.source.is_dir():
            raise ValueError(f"Source path is not a directory: {self.source}")
        
        # Validate source archive structure
        games_path = self.source / 'Games'
        metadata_path = self.source / 'Metadata'
        
        if not games_path.exists():
            raise ValueError(
                f"Invalid master archive structure: 'Games' directory not found at {games_path}\n"
                f"Expected structure: {self.source}/Games/[Platform]/[games]"
            )
        
        if not metadata_path.exists():
            self.logger.warning(
                f"Metadata directory not found at {metadata_path}\n"
                f"Metadata export will be skipped unless this directory exists"
            )
        
        # Create destination if it doesn't exist
        try:
            if not self.dry_run:
                self.destination.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Destination directory ready: {self.destination}")
            else:
                self.logger.info(f"DRY-RUN: Would create destination directory: {self.destination}")
        except PermissionError as e:
            raise ValueError(
                f"Permission denied when creating destination directory: {self.destination}\n"
                f"Error: {e}\n"
                f"Please check directory permissions or run with appropriate privileges"
            )
        except Exception as e:
            raise ValueError(
                f"Failed to create destination directory: {self.destination}\n"
                f"Error: {e}"
            )
        
        # Validate and create custom systems directory if specified
        if self.custom_systems_path:
            custom_systems_file = Path(self.custom_systems_path).expanduser()
            custom_systems_dir = custom_systems_file.parent
            
            try:
                if not self.dry_run:
                    if not custom_systems_dir.exists():
                        custom_systems_dir.mkdir(parents=True, exist_ok=True)
                        self.logger.info(f"Created custom systems directory: {custom_systems_dir}")
                    
                    # Create template XML file if it doesn't exist
                    if not custom_systems_file.exists():
                        xml_content = '<?xml version="1.0"?>\n<systemList>\n</systemList>\n'
                        custom_systems_file.write_text(xml_content)
                        self.logger.info(f"Created custom systems template: {custom_systems_file}")
                else:
                    if not custom_systems_dir.exists():
                        self.logger.info(f"DRY-RUN: Would create custom systems directory: {custom_systems_dir}")
                    if not custom_systems_file.exists():
                        self.logger.info(f"DRY-RUN: Would create custom systems file: {custom_systems_file}")
            except PermissionError as e:
                raise ValueError(
                    f"Permission denied when creating custom systems directory: {custom_systems_dir}\n"
                    f"Error: {e}\n"
                    f"Please check directory permissions or run with appropriate privileges"
                )
            except Exception as e:
                self.logger.warning(
                    f"Could not create custom systems directory: {custom_systems_dir}\n"
                    f"Error: {e}\n"
                    f"Custom system additions may fail"
                )
        
        self.logger.info(f"Source: {self.source}")
        self.logger.info(f"Destination: {self.destination}")
    
    def _load_config(self):
        """Load configuration from file"""
        try:
            config_file = Path(self.config_path)
            if config_file.exists():
                with open(config_file, 'r') as f:
                    self.config = json.load(f)
                self.logger.info(f"Loaded configuration from {self.config_path}")
            else:
                self.logger.warning(f"Config file not found: {self.config_path}")
        except Exception as e:
            self.logger.error(f"Error loading config: {e}")
            self.config = {}
    
    def _indent_xml(self, elem, level=0):
        """
        Add indentation to XML for pretty printing
        
        Args:
            elem: XML element to indent
            level: Current indentation level
        """
        indent = "\n" + "  " * level
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = indent + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = indent
            for child in elem:
                self._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent
    
    def _format_system_element(self, system_elem) -> str:
        """
        Format a system XML element as a readable string
        
        Args:
            system_elem: XML Element to format
            
        Returns:
            Formatted XML string
        """
        import xml.etree.ElementTree as ET
        
        lines = ["  <system>"]
        for child in system_elem:
            if child.text:
                lines.append(f"    <{child.tag}>{child.text}</{child.tag}>")
            else:
                lines.append(f"    <{child.tag} />")
        lines.append("  </system>")
        
        return "\n".join(lines)
    
    def get_available_platforms(self) -> List[str]:
        """
        Get list of all available platforms in the master archive
        
        Returns:
            List of platform names
        """
        if self.available_platforms:
            return list(self.available_platforms.keys())
        
        games_path = self.source / 'Games'
        if not games_path.exists():
            self.logger.error(f"Games directory not found: {games_path}")
            return []
        
        platforms = []
        for item in games_path.iterdir():
            if item.is_dir():
                platforms.append(item.name)
                self.available_platforms[item.name] = item
        
        platforms.sort()
        return platforms
    
    def fuzzy_match_platform(self, query: str, threshold: float = 0.6) -> List[Tuple[str, float]]:
        """
        Find platforms matching the query using fuzzy matching
        
        Args:
            query: Search query
            threshold: Minimum similarity score (0-1)
            
        Returns:
            List of (platform_name, score) tuples sorted by score
        """
        platforms = self.get_available_platforms()
        matches = []
        
        query_lower = query.lower()
        
        for platform in platforms:
            platform_lower = platform.lower()
            
            # Exact match
            if query_lower == platform_lower:
                matches.append((platform, 1.0))
                continue
            
            # Contains match
            if query_lower in platform_lower:
                matches.append((platform, 0.9))
                continue
            
            # Fuzzy match using SequenceMatcher
            ratio = SequenceMatcher(None, query_lower, platform_lower).ratio()
            if ratio >= threshold:
                matches.append((platform, ratio))
        
        # Sort by score descending
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches
    
    def select_platform_interactive(self, query: Optional[str] = None) -> Optional[str]:
        """
        Interactively select a platform
        
        Args:
            query: Optional search query for fuzzy matching
            
        Returns:
            Selected platform name or None
        """
        if query and query.upper() != 'ALL':
            # Fuzzy match
            matches = self.fuzzy_match_platform(query)
            
            if not matches:
                print(f"No platforms found matching '{query}'")
                return None
            
            # Always show matches for user confirmation (even if only one)
            print(f"\nFound {len(matches)} platform(s) matching '{query}':")
            for idx, (platform, score) in enumerate(matches, 1):
                print(f"  {idx}. {platform} (match: {score:.0%})")
            
            # Auto-select if only one match and it's exact
            if len(matches) == 1 and matches[0][1] == 1.0:
                platform = matches[0][0]
                confirm = input(f"\nUse '{platform}'? (Y/n): ").strip().lower()
                if confirm in ['', 'y', 'yes']:
                    print(f"Selected platform: {platform}")
                    return platform
                else:
                    return None
            
            # Multiple matches or non-exact - let user choose
            while True:
                try:
                    choice = input("\nSelect platform number (or 'q' to quit): ").strip()
                    if choice.lower() == 'q':
                        return None
                    
                    idx = int(choice) - 1
                    if 0 <= idx < len(matches):
                        return matches[idx][0]
                    else:
                        print("Invalid selection. Try again.")
                except ValueError:
                    print("Please enter a valid number.")
        else:
            # Show all platforms
            platforms = self.get_available_platforms()
            
            print(f"\nAvailable platforms ({len(platforms)}):")
            for idx, platform in enumerate(platforms, 1):
                print(f"  {idx}. {platform}")
            
            while True:
                try:
                    choice = input("\nSelect platform number (or 'q' to quit): ").strip()
                    if choice.lower() == 'q':
                        return None
                    
                    idx = int(choice) - 1
                    if 0 <= idx < len(platforms):
                        return platforms[idx]
                    else:
                        print("Invalid selection. Try again.")
                except ValueError:
                    print("Please enter a valid number.")
    
    def select_platforms_multi_interactive(self) -> List[str]:
        """
        Interactively select multiple platforms to export
        Step through each platform and choose which ones to include
        
        Returns:
            List of selected platform names
        """
        platforms = self.get_available_platforms()
        selected_platforms = []
        
        print(f"\n{'='*70}")
        print(f"Interactive Platform Selection")
        print(f"Total platforms: {len(platforms)}")
        print(f"{'='*70}")
        print("\nFor each platform, choose:")
        print("  y/yes - Export this platform")
        print("  n/no  - Skip this platform")
        print("  a/all - Export all remaining platforms")
        print("  q/quit - Stop and export selected platforms so far")
        print(f"{'='*70}\n")
        
        for idx, platform in enumerate(platforms, 1):
            print(f"\n[{idx}/{len(platforms)}] {platform}")
            
            while True:
                choice = input("  Export? (y/n/a/q): ").strip().lower()
                
                if choice in ['y', 'yes']:
                    selected_platforms.append(platform)
                    print(f"  ✓ Added to export list")
                    break
                elif choice in ['n', 'no']:
                    print(f"  ✗ Skipped")
                    break
                elif choice in ['a', 'all']:
                    # Add current platform and all remaining
                    selected_platforms.extend(platforms[idx-1:])
                    print(f"  ✓ Added all remaining {len(platforms) - idx + 1} platforms")
                    return selected_platforms
                elif choice in ['q', 'quit']:
                    print(f"\nStopping selection. {len(selected_platforms)} platforms selected.")
                    return selected_platforms
                else:
                    print("  Invalid input. Please enter y/n/a/q")
        
        print(f"\nSelected {len(selected_platforms)} platforms")
        return selected_platforms
    
    def scan_platform_games(self, platform_name: str) -> List[Dict[str, any]]:
        """
        Scan all games for a specific platform
        
        Args:
            platform_name: Name of the platform
            
        Returns:
            List of game dictionaries with metadata
        """
        if platform_name in self.platform_games:
            return self.platform_games[platform_name]
        
        platform_path = self.source / 'Games' / platform_name
        if not platform_path.exists():
            self.logger.error(f"Platform directory not found: {platform_path}")
            return []
        
        games = []
        for item in platform_path.iterdir():
            if item.is_file():
                game_info = {
                    'name': item.stem,  # Filename without extension
                    'filename': item.name,
                    'path': item,
                    'size': item.stat().st_size,
                    'extension': item.suffix
                }
                games.append(game_info)
        
        # Sort by name
        games.sort(key=lambda x: x['name'].lower())
        
        self.platform_games[platform_name] = games
        return games
    
    def fuzzy_match_games(self, platform_name: str, query: str, threshold: float = 0.6) -> List[Tuple[Dict, float]]:
        """
        Find games matching the query using fuzzy matching
        
        Args:
            platform_name: Platform to search in
            query: Search query
            threshold: Minimum similarity score (0-1)
            
        Returns:
            List of (game_info, score) tuples sorted by score
        """
        games = self.scan_platform_games(platform_name)
        matches = []
        
        query_lower = query.lower()
        
        for game in games:
            game_name_lower = game['name'].lower()
            
            # Exact match
            if query_lower == game_name_lower:
                matches.append((game, 1.0))
                continue
            
            # Contains match
            if query_lower in game_name_lower:
                matches.append((game, 0.9))
                continue
            
            # Fuzzy match
            ratio = SequenceMatcher(None, query_lower, game_name_lower).ratio()
            if ratio >= threshold:
                matches.append((game, ratio))
        
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches
    
    def select_games_interactive(self, platform_name: str, query: Optional[str] = None) -> List[Dict]:
        """
        Interactively select games to export
        
        Args:
            platform_name: Platform name
            query: Optional search query for specific game(s)
            
        Returns:
            List of selected game info dictionaries
        """
        games = self.scan_platform_games(platform_name)
        
        if not games:
            print(f"No games found for platform: {platform_name}")
            return []
        
        if query and query.upper() != 'ALL' and query.upper() != 'INTERACTIVE':
            # Fuzzy match specific game
            matches = self.fuzzy_match_games(platform_name, query)
            
            if not matches:
                print(f"No games found matching '{query}'")
                return []
            
            # Always show matches for user confirmation (even if only one)
            print(f"\nFound {len(matches)} game(s) matching '{query}':")
            for idx, (game, score) in enumerate(matches, 1):
                size_mb = game['size'] / (1024 * 1024)
                print(f"  {idx}. {game['name']} ({size_mb:.2f} MB) (match: {score:.0%})")
            
            print(f"  {len(matches) + 1}. Select all matches")
            
            # Auto-select if only one match and it's exact
            if len(matches) == 1 and matches[0][1] == 1.0:
                game = matches[0][0]
                confirm = input(f"\nUse '{game['name']}'? (Y/n): ").strip().lower()
                if confirm in ['', 'y', 'yes']:
                    print(f"Selected game: {game['name']}")
                    return [game]
                else:
                    return []
            
            # Multiple matches or non-exact - let user choose
            while True:
                try:
                    choice = input("\nSelect game number (or 'q' to quit): ").strip()
                    if choice.lower() == 'q':
                        return []
                    
                    idx = int(choice) - 1
                    if idx == len(matches):
                        # Select all matches
                        print(f"Selected all {len(matches)} matching games")
                        return [m[0] for m in matches]
                    elif 0 <= idx < len(matches):
                        return [matches[idx][0]]
                    else:
                        print("Invalid selection. Try again.")
                except ValueError:
                    print("Please enter a valid number.")
        
        elif query and query.upper() == 'INTERACTIVE':
            # Interactive mode - step through each game
            selected_games = []
            
            print(f"\n{'='*70}")
            print(f"Interactive mode: {platform_name}")
            print(f"Total games: {len(games)}")
            print(f"{'='*70}")
            print("\nFor each game, choose:")
            print("  y/yes - Export this game")
            print("  n/no  - Skip this game")
            print("  a/all - Export all remaining games")
            print("  q/quit - Stop and export selected games so far")
            print(f"{'='*70}\n")
            
            for idx, game in enumerate(games, 1):
                size_mb = game['size'] / (1024 * 1024)
                print(f"\n[{idx}/{len(games)}] {game['name']}")
                print(f"  File: {game['filename']} ({size_mb:.2f} MB)")
                
                while True:
                    choice = input("  Export? (y/n/a/q): ").strip().lower()
                    
                    if choice in ['y', 'yes']:
                        selected_games.append(game)
                        print(f"  ✓ Added to export list")
                        break
                    elif choice in ['n', 'no']:
                        print(f"  ✗ Skipped")
                        break
                    elif choice in ['a', 'all']:
                        # Add current game and all remaining
                        selected_games.extend(games[idx-1:])
                        print(f"  ✓ Added all remaining {len(games) - idx + 1} games")
                        return selected_games
                    elif choice in ['q', 'quit']:
                        print(f"\nStopping selection. {len(selected_games)} games selected.")
                        return selected_games
                    else:
                        print("  Invalid input. Please enter y/n/a/q")
            
            return selected_games
        
        else:
            # ALL - return all games
            print(f"Selected all {len(games)} games from {platform_name}")
            return games
    
    def create_symlink(self, source: Path, destination: Path, force: bool = False) -> bool:
        """
        Create a symlink or copy from source to destination
        
        Args:
            source: Source file path
            destination: Destination symlink/file path
            force: Whether to overwrite existing files
            
        Returns:
            True if successful, False otherwise
        """
        try:
            operation = "symlink" if self.use_symlinks else "copy"
            
            # Validate source exists
            if not source.exists():
                self.logger.error(f"Source file does not exist: {source}")
                return False
            
            if not source.is_file():
                self.logger.error(f"Source is not a file: {source}")
                return False
            
            # Dry run mode - simulate without creating
            if self.dry_run:
                # Log with full paths in verbose mode, short names otherwise
                if self.verbose:
                    self.logger.info(f"[DRY RUN] Would {operation}: {destination} -> {source}")
                else:
                    self.logger.debug(f"[DRY RUN] Would {operation}: {destination.name} -> {source}")
                
                # Check if destination would be overwritten
                if destination.exists() or destination.is_symlink():
                    if force:
                        self.logger.debug(f"[DRY RUN] Would remove existing file: {destination}")
                    else:
                        self.logger.warning(f"[DRY RUN] Destination exists (would skip): {destination.name}")
                        return False
                return True
            
            # Create parent directories if needed
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.logger.error(f"Failed to create parent directory {destination.parent}: {e}")
                return False
            
            # Check if destination already exists
            if destination.exists() or destination.is_symlink():
                if force:
                    try:
                        destination.unlink()
                        self.logger.debug(f"Removed existing file: {destination}")
                    except Exception as e:
                        self.logger.error(f"Failed to remove existing file {destination}: {e}")
                        return False
                else:
                    self.logger.warning(f"Destination already exists (skipping): {destination.name}")
                    return False
            
            # Create symlink or copy file
            if self.use_symlinks:
                try:
                    os.symlink(source, destination)
                    
                    # Log with full paths in verbose mode, short names otherwise
                    if self.verbose:
                        self.logger.info(f"Created symlink: {destination} -> {source}")
                    else:
                        self.logger.debug(f"Created symlink: {destination.name} -> {source}")
                    
                    # Verify symlink was created successfully
                    if not destination.is_symlink():
                        self.logger.error(f"Symlink creation reported success but link does not exist: {destination}")
                        return False
                    
                    # Verify symlink points to the correct source
                    if destination.resolve() != source.resolve():
                        self.logger.error(
                            f"Symlink created but points to wrong target: "
                            f"{destination.resolve()} != {source.resolve()}"
                        )
                        return False
                        
                except OSError as e:
                    # Check if it's a privilege error on Windows
                    if e.winerror == 1314:  # ERROR_PRIVILEGE_NOT_HELD
                        self.logger.error(
                            f"Symlink creation failed: Insufficient privileges\n"
                            f"  On Windows, you need either:\n"
                            f"  1. Run as Administrator, OR\n"
                            f"  2. Enable Developer Mode (Settings > Update & Security > For Developers)\n"
                            f"  Alternatively, use --symlink=false to copy files instead"
                        )
                    else:
                        self.logger.error(f"Failed to create symlink {destination}: {e}")
                    return False
            else:
                try:
                    import shutil
                    shutil.copy2(source, destination)
                    
                    # Log with full paths in verbose mode, short names otherwise
                    if self.verbose:
                        self.logger.info(f"Copied file: {destination} <- {source}")
                    else:
                        self.logger.debug(f"Copied file: {destination.name} <- {source}")
                    
                    # Verify file was copied successfully
                    if not destination.exists():
                        self.logger.error(f"File copy reported success but file does not exist: {destination}")
                        return False
                    
                    # Verify file size matches
                    source_size = source.stat().st_size
                    dest_size = destination.stat().st_size
                    if source_size != dest_size:
                        self.logger.error(
                            f"File copy size mismatch: source={source_size} bytes, "
                            f"dest={dest_size} bytes for {destination}"
                        )
                        return False
                        
                except Exception as e:
                    self.logger.error(f"Failed to copy file {source} to {destination}: {e}")
                    return False
            
            return True
            
        except OSError as e:
            self.logger.error(f"OS Error creating {operation} {destination}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error creating {operation}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def export_games(self, platform_name: str, games: List[Dict], force: bool = False) -> Dict[str, int]:
        """
        Export selected games for a platform
        
        Args:
            platform_name: Platform name (from master archive)
            games: List of game info dictionaries
            force: Whether to overwrite existing files
            
        Returns:
            Dictionary with export statistics
        """
        stats = {
            'attempted': len(games),
            'success': 0,
            'skipped': 0,
            'failed': 0,
            'total_size': 0
        }
        
        # Map platform name to destination format name
        mapped_platform = self.map_platform_name(platform_name)
        
        if not mapped_platform:
            # Check if it already exists as a custom system from a previous run
            if self.dest_format == 'es-de':
                mapped_platform = self.check_existing_custom_system(platform_name)
                
                if mapped_platform:
                    print(f"ℹ Using existing custom system for '{platform_name}': {mapped_platform}")
                elif not self.dry_run:
                    # Offer to add as custom system
                    custom_system = self.prompt_add_custom_system(platform_name)
                    if custom_system:
                        if self.update_es_systems_xml(custom_system):
                            mapped_platform = custom_system['name']
                        else:
                            print(f"Failed to add custom system. Skipping platform: {platform_name}")
                            return stats
                    else:
                        print(f"Skipping platform: {platform_name}")
                        return stats
                else:
                    self.logger.error(f"No platform mapping for '{platform_name}' in dry-run mode")
                    print(f"✗ Skipping platform: {platform_name} (no mapping, cannot add in dry-run)")
                    return stats
            else:
                self.logger.error(f"No platform mapping for '{platform_name}' and cannot add custom system")
                print(f"✗ Skipping platform: {platform_name} (no mapping)")
                return stats
        
        # Create platform directory in destination based on format
        roms_base = self.format_config['roms_path']
        if roms_base:
            platform_dest = self.destination / roms_base / mapped_platform
        else:
            platform_dest = self.destination / mapped_platform
        
        platform_dest.mkdir(parents=True, exist_ok=True)
        
        dry_run_prefix = "[DRY RUN] " if self.dry_run else ""
        self.logger.info(f"\n{dry_run_prefix}Exporting {len(games)} games for {platform_name}...")
        
        for game in games:
            source_path = game['path']
            dest_path = platform_dest / game['filename']
            
            if self.create_symlink(source_path, dest_path, force):
                stats['success'] += 1
                stats['total_size'] += game['size']
                print(f"  ✓ {game['name']}")
            elif dest_path.exists() and not self.dry_run:
                stats['skipped'] += 1
                print(f"  ⊘ {game['name']} (already exists)")
            else:
                stats['failed'] += 1
                print(f"  ✗ {game['name']} (failed)")
        
        return stats
    
    def find_metadata(self, platform_name: str, game_name: str, metadata_type: str) -> List[Path]:
        """
        Find metadata files for a specific game
        
        Args:
            platform_name: Platform name
            game_name: Game name (without extension)
            metadata_type: Type of metadata (Images, Videos, Manuals, Music)
            
        Returns:
            List of matching metadata file paths
        """
        metadata_base = self.source / 'Metadata' / metadata_type / platform_name
        
        if not metadata_base.exists():
            return []
        
        matching_files = []
        
        # Search recursively for files matching the game name
        for file_path in metadata_base.rglob('*'):
            if file_path.is_file():
                # Check if filename starts with game name
                if file_path.stem.startswith(game_name):
                    matching_files.append(file_path)
        
        return matching_files
    
    def export_metadata(self, platform_name: str, games: List[Dict], 
                       metadata_types: Optional[List[str]] = None, 
                       force: bool = False) -> Dict[str, int]:
        """
        Export metadata for selected games using format-specific metadata mappings
        
        Args:
            platform_name: Platform name (from master archive)
            games: List of game info dictionaries
            metadata_types: List of metadata types to export (deprecated - uses mappings now)
            force: Whether to overwrite existing files
            
        Returns:
            Dictionary with metadata export statistics
        """
        stats = {
            'images': 0,
            'videos': 0,
            'manuals': 0,
            'music': 0,
            'skipped_unmapped': 0,
            'total': 0
        }
        
        # Map platform name
        mapped_platform = self.map_platform_name(platform_name)
        if not mapped_platform:
            self.logger.warning(f"Skipping metadata export for unmapped platform: {platform_name}")
            return stats
        
        # Get metadata mappings from format config
        metadata_mappings = self.format_config.get('metadata_mappings', {})
        if not metadata_mappings:
            self.logger.info(f"No metadata mappings configured for format: {self.dest_format}")
            return stats
        
        dry_run_prefix = "[DRY RUN] " if self.dry_run else ""
        self.logger.info(f"\n{dry_run_prefix}Exporting metadata for {platform_name} -> {mapped_platform}...")
        
        # Track unmapped directories we encounter
        unmapped_dirs = set()
        
        for game in games:
            game_name = game['name']
            
            # Process each metadata mapping
            for archive_path, dest_name in metadata_mappings.items():
                # Skip if destination is null (explicitly not supported)
                if dest_name is None:
                    continue
                
                # Parse archive path (e.g., "Images/Box - Front" or "Videos")
                path_parts = archive_path.split('/')
                metadata_type = path_parts[0]  # Images, Videos, Manuals, Music
                
                # Build the source path
                if len(path_parts) > 1:
                    # Has subdirectory (e.g., Images/Box - Front)
                    subdir = '/'.join(path_parts[1:])
                    metadata_base = self.source / 'Metadata' / metadata_type / platform_name / subdir
                else:
                    # No subdirectory (e.g., Videos, Manuals)
                    metadata_base = self.source / 'Metadata' / metadata_type / platform_name
                
                # Check if this metadata directory exists
                if not metadata_base.exists():
                    if archive_path not in unmapped_dirs:
                        self.logger.debug(f"Metadata directory not found: {metadata_base}")
                        unmapped_dirs.add(archive_path)
                    continue
                
                # Find matching files for this game
                matching_files = self._find_metadata_files(metadata_base, game_name)
                
                if not matching_files:
                    continue
                
                # If multiple files found, let user choose (unless in non-interactive mode)
                selected_file = None
                if len(matching_files) == 1:
                    selected_file = matching_files[0]
                else:
                    # Multiple files - need to choose one
                    selected_file = self._select_metadata_file(
                        matching_files, 
                        game_name, 
                        archive_path, 
                        dest_name
                    )
                
                if not selected_file:
                    continue
                
                # Build destination path
                # ES-DE format: [platform]/images/[gamename]-[type].ext
                dest_filename = f"{game_name}-{dest_name}{selected_file.suffix}"
                
                if self.format_config['metadata_subdir']:
                    # Metadata within ROM directory
                    roms_base = self.format_config.get('roms_path', '')
                    if roms_base:
                        dest_path = self.destination / roms_base / mapped_platform / 'images' / dest_filename
                    else:
                        dest_path = self.destination / mapped_platform / 'images' / dest_filename
                else:
                    # Separate metadata structure
                    dest_path = self.destination / 'metadata' / mapped_platform / 'images' / dest_filename
                
                # Create symlink/copy
                if self.create_symlink(selected_file, dest_path, force):
                    stats[metadata_type.lower()] += 1
                    stats['total'] += 1
        
        # Report any unmapped directories we found
        if unmapped_dirs:
            # Find directories that exist but aren't mapped
            metadata_base_path = self.source / 'Metadata'
            if metadata_base_path.exists():
                for metadata_type in ['Images', 'Videos', 'Manuals', 'Music']:
                    type_path = metadata_base_path / metadata_type / platform_name
                    if type_path.exists():
                        for subdir in type_path.iterdir():
                            if subdir.is_dir():
                                archive_path = f"{metadata_type}/{subdir.name}"
                                if archive_path not in metadata_mappings:
                                    self.logger.info(
                                        f"Skipping unmapped metadata directory: {archive_path} "
                                        f"(not supported by {self.dest_format})"
                                    )
                                    stats['skipped_unmapped'] += 1
        
        return stats
    
    def _find_metadata_files(self, base_path: Path, game_name: str) -> List[Path]:
        """
        Find metadata files matching the game name
        
        Args:
            base_path: Base directory to search
            game_name: Game name to match
            
        Returns:
            List of matching file paths
        """
        matching_files = []
        
        if not base_path.exists():
            return matching_files
        
        # Search for files in this directory (not recursive for mapped dirs)
        for file_path in base_path.iterdir():
            if file_path.is_file():
                # Check if filename starts with game name
                if file_path.stem.startswith(game_name):
                    matching_files.append(file_path)
        
        return matching_files
    
    def _select_metadata_file(self, files: List[Path], game_name: str, 
                              archive_path: str, dest_name: str) -> Optional[Path]:
        """
        Let user select which metadata file to use when multiple exist
        
        Args:
            files: List of candidate files
            game_name: Game name
            archive_path: Archive metadata path
            dest_name: Destination metadata name
            
        Returns:
            Selected file path, or None if skipped
        """
        if not files:
            return None
        
        # In non-interactive mode, dry-run, or auto-select mode, just take the first file
        if self.dry_run:
            self.logger.info(
                f"Multiple {archive_path} files for '{game_name}': "
                f"would use {files[0].name}"
            )
            return files[0]
        
        if self.auto_select_metadata:
            return files[0]
        
        print(f"\n{'='*70}")
        print(f"Multiple {archive_path} files found for: {game_name}")
        print(f"Destination allows only one file as: {dest_name}")
        print(f"{'='*70}")
        
        for i, file_path in enumerate(files, 1):
            size_kb = file_path.stat().st_size / 1024
            print(f"  {i}. {file_path.name} ({size_kb:.1f} KB)")
        
        print(f"  s. Skip this metadata")
        print(f"  a. Always use first file (stop prompting)")
        
        while True:
            choice = input(f"\nSelect file [1-{len(files)}/s/a]: ").strip().lower()
            
            if choice == 's':
                return None
            elif choice == 'a':
                # Set a flag to auto-select first file from now on
                self.auto_select_metadata = True
                return files[0]
            elif choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(files):
                    return files[index]
            
            print("Invalid choice. Please try again.")
    
    def generate_report(self, platform_stats: Dict[str, Dict]) -> str:
        """
        Generate a summary report of the export
        
        Args:
            platform_stats: Dictionary of statistics per platform
            
        Returns:
            Formatted report string
        """
        report = []
        report.append("\n" + "=" * 70)
        if self.dry_run:
            report.append("EXPORT SUMMARY REPORT (DRY RUN)")
        else:
            report.append("EXPORT SUMMARY REPORT")
        report.append("=" * 70)
        
        total_games = 0
        total_size = 0
        total_failed = 0
        total_skipped = 0
        
        for platform, stats in platform_stats.items():
            report.append(f"\n{platform}:")
            report.append(f"  Games exported: {stats['success']}/{stats['attempted']}")
            if stats['skipped'] > 0:
                report.append(f"  Skipped (already exist): {stats['skipped']}")
            if stats['failed'] > 0:
                report.append(f"  ✗ Failed: {stats['failed']}")
            
            size_mb = stats['total_size'] / (1024 * 1024)
            size_gb = size_mb / 1024
            
            if size_gb >= 1:
                report.append(f"  Total size: {size_gb:.2f} GB")
            else:
                report.append(f"  Total size: {size_mb:.2f} MB")
            
            total_games += stats['success']
            total_size += stats['total_size']
            total_failed += stats['failed']
            total_skipped += stats['skipped']
        
        report.append("\n" + "-" * 70)
        if self.dry_run:
            report.append(f"TOTAL GAMES THAT WOULD BE EXPORTED: {total_games}")
        else:
            report.append(f"TOTAL GAMES EXPORTED: {total_games}")
        
        if total_skipped > 0:
            report.append(f"Total skipped (already exist): {total_skipped}")
        
        if total_failed > 0:
            report.append(f"✗ TOTAL FAILED: {total_failed}")
            report.append(f"")
            report.append(f"Check the log file 'archive_export.log' for details on failures.")
            if self.use_symlinks and not self.dry_run:
                report.append(f"Note: On Windows, symlink creation requires:")
                report.append(f"  - Administrator privileges, OR")
                report.append(f"  - Developer Mode enabled")
                report.append(f"  Alternatively, use --symlink=false to copy files instead")
        
        total_gb = total_size / (1024 * 1024 * 1024)
        report.append(f"TOTAL SIZE: {total_gb:.2f} GB")
        
        if self.dry_run:
            report.append(f"Note: DRY RUN - No files were created")
        elif self.use_symlinks:
            report.append(f"Note: Using symlinks - actual disk space used is minimal")
        else:
            report.append(f"Note: Files were copied - {total_gb:.2f} GB of disk space used")
        report.append("=" * 70)
        
        return "\n".join(report)


def main():
    """Main entry point for the script"""
    parser = argparse.ArgumentParser(
        description='Export games and metadata from master archive using symlinks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all available destination formats
  python init.py --list-formats
  
  # Show platform mappings for ES-DE
  python init.py --show-mappings es-de
  
  # Use format defaults (ES-DE: ~/.emulationstation/ROMs)
  python init.py --platform "nes" --games ALL
  
  # Interactive platform selection, export all games (uses format default)
  python init.py
  
  # Override destination directory
  python init.py --dest /home/user/games --platform ALL --games ALL
  
  # Export with custom destination (positional argument)
  python init.py /mnt/Emulators/Master\\ Archive /home/user/custom_games --platform "snes" --games ALL
  
  # Interactive platform selection, then export all games from selected platforms
  python init.py --dest /home/user/games --platform INTERACTIVE --games ALL
  
  # Export specific platform with fuzzy matching, interactive game selection
  python init.py --dest /home/user/games --platform "snes" --games INTERACTIVE
  
  # Export specific platform and game with fuzzy matching
  python init.py --dest /home/user/games --platform "genesis" --games "sonic"
  
  # Specify destination format (currently only es-de available)
  python init.py --format es-de --platform "snes" --games ALL
  
  # Dry run - preview what would be exported without creating files
  python init.py --dry-run --platform "nes" --games ALL
  
  # Verbose mode - see detailed logging with custom destination
  python init.py --dest /home/user/games --verbose --platform "genesis" --games INTERACTIVE
  
  # Copy files instead of creating symlinks
  python init.py --symlink false --platform "nes" --games ALL
  
  # Combine options: copy mode with dry-run to preview disk space needed
  python init.py --symlink false --dry-run --platform ALL --games ALL
  
  # Combine dry-run and verbose for detailed preview
  python init.py --dry-run --verbose --platform ALL --games ALL
        """
    )
    
    parser.add_argument(
        'source',
        nargs='?',
        default=ArchiveExporter.DEFAULT_ARCHIVE_PATH,
        help=f'Path to master archive (default: {ArchiveExporter.DEFAULT_ARCHIVE_PATH})'
    )
    
    parser.add_argument(
        'destination',
        nargs='?',
        help='Destination directory for export (optional - uses format default if not specified)'
    )
    
    parser.add_argument(
        '--dest',
        '--override-destination',
        dest='dest_override',
        help='Override destination directory (alternative to positional argument)'
    )
    
    parser.add_argument(
        '--format',
        default='es-de',
        help='Destination format (default: es-de). Available formats are loaded from fe_formats.json'
    )
    
    parser.add_argument(
        '--list-formats',
        action='store_true',
        help='List all available destination formats and exit'
    )
    
    parser.add_argument(
        '--show-mappings',
        metavar='FORMAT',
        help='Show platform mappings for specified format and exit'
    )
    
    parser.add_argument(
        '--platform',
        help='Platform to export: "ALL" (all platforms), "INTERACTIVE" (step through), or fuzzy match name'
    )
    
    parser.add_argument(
        '--games',
        help='Games to export: "ALL" (all games), "INTERACTIVE" (step through), or fuzzy match game name'
    )
    
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force overwrite existing files'
    )
    
    parser.add_argument(
        '--no-metadata',
        action='store_true',
        help='Skip exporting metadata files'
    )
    
    parser.add_argument(
        '--metadata-types',
        nargs='+',
        choices=['Images', 'Videos', 'Manuals', 'Music'],
        default=['Images', 'Videos', 'Manuals'],
        help='Types of metadata to export'
    )
    
    parser.add_argument(
        '--config',
        help='Path to configuration file'
    )
    
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Enable verbose output (detailed logging)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simulate export without creating any symlinks (preview mode)'
    )
    
    parser.add_argument(
        '--symlink',
        type=lambda x: x.lower() in ('true', 'yes', '1'),
        default=True,
        metavar='BOOL',
        help='Create symlinks (true) or copy files (false). Default: true'
    )
    
    args = parser.parse_args()
    
    # Handle --list-formats
    if args.list_formats:
        try:
            # Load formats to display them
            script_dir = Path(__file__).parent
            config_file = script_dir / ArchiveExporter.FORMATS_CONFIG_FILE
            
            if not config_file.exists():
                config_file = Path(ArchiveExporter.FORMATS_CONFIG_FILE)
            
            with open(config_file, 'r') as f:
                config_data = json.load(f)
            
            formats = config_data.get('formats', {})
            
            print("\nAvailable destination formats:")
            print("=" * 70)
            for fmt_id, fmt_config in formats.items():
                print(f"\n{fmt_id}:")
                print(f"  Name: {fmt_config['name']}")
                print(f"  Default destination: {fmt_config['default_destination']}")
                print(f"  Description: {fmt_config['description']}")
                mappings_count = len(fmt_config.get('platform_mappings', {}))
                print(f"  Platform mappings: {mappings_count}")
            print("\n" + "=" * 70)
            return 0
            
        except Exception as e:
            print(f"Error loading formats: {e}", file=sys.stderr)
            return 1
    
    # Handle --show-mappings
    if args.show_mappings:
        try:
            script_dir = Path(__file__).parent
            config_file = script_dir / ArchiveExporter.FORMATS_CONFIG_FILE
            
            if not config_file.exists():
                config_file = Path(ArchiveExporter.FORMATS_CONFIG_FILE)
            
            with open(config_file, 'r') as f:
                config_data = json.load(f)
            
            formats = config_data.get('formats', {})
            fmt_config = formats.get(args.show_mappings)
            
            if not fmt_config:
                print(f"Error: Format '{args.show_mappings}' not found", file=sys.stderr)
                print(f"Available formats: {', '.join(formats.keys())}")
                return 1
            
            mappings = fmt_config.get('platform_mappings', {})
            
            print(f"\nPlatform mappings for {fmt_config['name']}:")
            print("=" * 70)
            print(f"{'Master Archive Platform':<50} | Destination")
            print("-" * 70)
            
            for archive_name, dest_name in sorted(mappings.items()):
                print(f"{archive_name:<50} | {dest_name}")
            
            print("=" * 70)
            print(f"Total mappings: {len(mappings)}")
            return 0
            
        except Exception as e:
            print(f"Error showing mappings: {e}", file=sys.stderr)
            return 1
    
    # Determine destination (priority: --dest > positional > format default)
    destination = args.dest_override or args.destination
    
    # Note: destination can be None - will use format default
    if destination:
        print(f"Using custom destination: {destination}")
    else:
        # Show what the default will be - need to load formats first
        try:
            script_dir = Path(__file__).parent
            config_file = script_dir / ArchiveExporter.FORMATS_CONFIG_FILE
            
            if not config_file.exists():
                config_file = Path(ArchiveExporter.FORMATS_CONFIG_FILE)
            
            with open(config_file, 'r') as f:
                config_data = json.load(f)
            
            formats = config_data.get('formats', {})
            fmt_config = formats.get(args.format)
            
            if fmt_config:
                print(f"Using default destination for {args.format}: {fmt_config['default_destination']}")
            else:
                print(f"Warning: Format '{args.format}' not found in configuration")
        except Exception:
            pass  # Will be caught during exporter initialization
    
    try:
        # Create exporter instance
        exporter = ArchiveExporter(
            source_path=args.source,
            destination_path=destination,
            dest_format=args.format,
            config_path=args.config,
            dry_run=args.dry_run,
            verbose=args.verbose,
            use_symlinks=args.symlink
        )
        
        print("\n" + "=" * 70)
        print("MASTER ARCHIVE EXPORT TOOL")
        if args.dry_run:
            print("*** DRY RUN MODE - NO FILES WILL BE CREATED ***")
        print("=" * 70)
        print(f"Format: {exporter.format_config['name']}")
        print(f"Destination: {exporter.destination}")
        print(f"Mode: {'Symlinks' if args.symlink else 'Copy files'}")
        if args.verbose:
            print("Verbose logging: ENABLED")
        
        # Select platform(s)
        platforms_to_export = []
        
        if args.platform:
            if args.platform.upper() == 'ALL':
                platforms_to_export = exporter.get_available_platforms()
                print(f"\nExporting ALL platforms ({len(platforms_to_export)} total)")
            elif args.platform.upper() == 'INTERACTIVE':
                # Interactive selection - choose multiple platforms
                platforms_to_export = exporter.select_platforms_multi_interactive()
                if not platforms_to_export:
                    print("No platforms selected. Exiting.")
                    return 0
            else:
                # Fuzzy match platform
                selected = exporter.select_platform_interactive(args.platform)
                if selected:
                    platforms_to_export = [selected]
                else:
                    print("No platform selected. Exiting.")
                    return 0
        else:
            # Interactive platform selection (single)
            selected = exporter.select_platform_interactive()
            if selected:
                platforms_to_export = [selected]
            else:
                print("No platform selected. Exiting.")
                return 0
        
        # Process each platform
        all_platform_stats = {}
        
        for platform in platforms_to_export:
            print(f"\n{'='*70}")
            print(f"Platform: {platform}")
            print(f"{'='*70}")
            
            # Select games
            if args.games:
                games_to_export = exporter.select_games_interactive(platform, args.games)
            else:
                # Default to ALL
                games_to_export = exporter.select_games_interactive(platform, 'ALL')
            
            if not games_to_export:
                print(f"No games selected for {platform}. Skipping.")
                continue
            
            # Export games
            stats = exporter.export_games(platform, games_to_export, force=args.force)
            all_platform_stats[platform] = stats
            
            # Export metadata if requested
            if not args.no_metadata and stats['success'] > 0:
                metadata_stats = exporter.export_metadata(
                    platform, 
                    games_to_export, 
                    metadata_types=args.metadata_types,
                    force=args.force
                )
                print(f"\nMetadata exported:")
                for mtype, count in metadata_stats.items():
                    if count > 0 and mtype != 'total':
                        print(f"  {mtype}: {count} files")
        
        # Generate and print report
        if all_platform_stats:
            print(exporter.generate_report(all_platform_stats))
        
        # Show unmapped platforms summary
        if exporter.unmapped_platforms:
            print("\n" + "!" * 70)
            print("UNMAPPED PLATFORMS")
            print("!" * 70)
            print("The following platforms were not mapped and were skipped:")
            for platform in exporter.unmapped_platforms:
                print(f"  - {platform}")
            print("\nTo add support for these platforms:")
            print(f"  1. Edit fe_formats.json and add mappings in 'platform_mappings'")
            print(f"  2. Or run without --dry-run to interactively add as custom systems")
            print("!" * 70)
        
        if args.dry_run:
            print("\n✓ Dry run completed successfully! (No files were created)")
        else:
            print("\n✓ Export completed successfully!")
        return 0
        
    except KeyboardInterrupt:
        print("\n\nExport cancelled by user.")
        return 130
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
