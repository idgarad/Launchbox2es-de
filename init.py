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
                 dry_run: bool = False, verbose: bool = False, use_symlinks: bool = True,
                 backport: bool = False):
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
            backport: If True, copy metadata from destination back to archive if missing
        """
        self.source = Path(source_path)
        self.dest_format = dest_format.lower()
        self.config_path = config_path
        self.config = {}
        self.dry_run = dry_run
        self.verbose = verbose
        self.use_symlinks = use_symlinks
        self.backport = backport
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
        
        # Check if custom metadata path is configured
        metadata_path = self.format_config.get('metadata_path')
        if metadata_path:
            metadata_path_expanded = Path(metadata_path).expanduser()
            self.logger.info(f"Using custom metadata path: {metadata_path_expanded}")
        
        # Cache for scanned data
        self.available_platforms = {}
        self.platform_games = {}
        self.unmapped_platforms = []  # Track platforms without mappings
        self.auto_select_metadata = False  # Flag for auto-selecting first metadata file
        self.metadata_subdir_cache = {}  # Cache subdirectory selections per archive_path
        self.global_metadata_subdirs = None  # Global list of selected subdirectories for all platforms
        self.metadata_subdirs_scanned = False  # Flag to track if we've done the global scan
        
        # XML metadata support
        self.xml_metadata = {}  # Cache for parsed XML metadata {platform: {game_name: metadata_dict}}
        
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
            # Check if it exists as a custom system/playlist before warning
            if self.dest_format == 'es-de':
                existing_system = self.check_existing_custom_system(archive_platform_name)
                if existing_system:
                    # Platform exists as custom system, no warning needed
                    return existing_system
            elif self.dest_format == 'retroarch':
                existing_playlist = self.check_existing_retroarch_playlist(archive_platform_name)
                if existing_playlist:
                    # Platform exists as custom playlist, no warning needed
                    return existing_playlist
            
            # Only warn if it's not a custom system/playlist
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
    
    def check_existing_retroarch_playlist(self, archive_platform_name: str) -> Optional[str]:
        """
        Check if a RetroArch playlist already exists for this platform
        
        Args:
            archive_platform_name: Platform name from master archive
            
        Returns:
            Playlist name if found, None otherwise
        """
        if not self.custom_systems_path or self.dest_format != 'retroarch':
            return None
        
        playlists_dir = Path(self.custom_systems_path).expanduser()
        
        if not playlists_dir.exists():
            return None
        
        try:
            # Look for .lpl files that might match
            for playlist_file in playlists_dir.glob('*.lpl'):
                playlist_name = playlist_file.stem
                
                # Check if the playlist name matches the archive platform name
                if playlist_name.lower() == archive_platform_name.lower().replace(' ', '_'):
                    self.logger.info(
                        f"Found existing RetroArch playlist for '{archive_platform_name}': {playlist_name}"
                    )
                    # Add to platform mappings for this session
                    self.platform_mappings[archive_platform_name] = playlist_name
                    return playlist_name
            
        except Exception as e:
            self.logger.warning(f"Error checking existing RetroArch playlists: {e}")
        
        return None
    
    def prompt_add_retroarch_playlist(self, archive_platform_name: str) -> Optional[Dict]:
        """
        Prompt user to add a RetroArch playlist for an unmapped platform
        
        Args:
            archive_platform_name: Platform name from master archive
            
        Returns:
            Dictionary with playlist information, or None if skipped
        """
        print(f"\n{'='*70}")
        print(f"UNMAPPED PLATFORM: {archive_platform_name}")
        print(f"{'='*70}")
        print(f"This platform is not mapped in the RetroArch configuration.")
        print(f"You can add it as a custom playlist.")
        print()
        
        choice = input("Create RetroArch playlist? (y/n): ").strip().lower()
        
        if choice not in ['y', 'yes']:
            print("Skipping platform.")
            return None
        
        # Gather playlist information
        print("\nEnter playlist information (press Enter for default):")
        
        # Suggest a playlist name based on archive name
        default_name = archive_platform_name.replace(' ', '_')
        playlist_name = input(f"Playlist name [{default_name}]: ").strip() or default_name
        
        full_name = input(f"Display name [{archive_platform_name}]: ").strip() or archive_platform_name
        
        # Suggest a default core
        print("\nRetroArch core (leave empty if unknown):")
        print("  Common cores: mame_libretro, nestopia_libretro, snes9x_libretro, etc.")
        default_core = input("Core name: ").strip()
        
        return {
            'name': playlist_name,
            'fullname': full_name,
            'default_core': default_core if default_core else 'DETECT',
            'archive_name': archive_platform_name
        }
    
    def update_retroarch_playlist(self, playlist_info: Dict) -> bool:
        """
        Create or update a RetroArch playlist (.lpl file)
        
        Args:
            playlist_info: Dictionary with playlist information
            
        Returns:
            True if successful, False otherwise
        """
        if not self.custom_systems_path:
            self.logger.error("No playlists path configured for RetroArch")
            return False
        
        playlists_dir = Path(self.custom_systems_path).expanduser()
        
        # Create the playlists directory if it doesn't exist
        playlists_dir.mkdir(parents=True, exist_ok=True)
        
        playlist_file = playlists_dir / f"{playlist_info['name']}.lpl"
        
        try:
            import json
            
            # Check if playlist already exists
            if playlist_file.exists():
                self.logger.warning(f"Playlist '{playlist_info['name']}' already exists")
                # Add to platform mappings for this session
                self.platform_mappings[playlist_info['archive_name']] = playlist_info['name']
                print(f"ℹ Playlist '{playlist_info['name']}.lpl' already exists, will add games to it")
                return True
            
            # Create new empty playlist
            # RetroArch playlists are JSON files with a specific structure
            playlist_data = {
                "version": "1.5",
                "default_core_path": "",
                "default_core_name": playlist_info.get('default_core', 'DETECT'),
                "label_display_mode": 0,
                "right_thumbnail_mode": 0,
                "left_thumbnail_mode": 0,
                "sort_mode": 0,
                "items": []
            }
            
            # Show preview
            print(f"\n{'='*70}")
            print("RETROARCH PLAYLIST TO BE CREATED:")
            print(f"{'='*70}")
            print(f"File: {playlist_file}")
            print(f"Display Name: {playlist_info['fullname']}")
            print(f"Default Core: {playlist_info.get('default_core', 'DETECT')}")
            print(f"{'='*70}")
            
            if self.dry_run:
                print(f"\nDRY-RUN: Would create playlist at {playlist_file}")
            else:
                print(f"\nTarget file: {playlist_file}")
            
            # Write playlist (unless dry-run)
            if not self.dry_run:
                with open(playlist_file, 'w', encoding='utf-8') as f:
                    json.dump(playlist_data, f, indent=2)
                
                self.logger.info(f"Created RetroArch playlist: {playlist_file}")
                print(f"\n✓ Successfully created playlist '{playlist_info['fullname']}'")
                print(f"  Playlist file: {playlist_file}")
                print(f"  Games will be added to this playlist during export")
            else:
                print(f"\n[DRY-RUN] Would create playlist '{playlist_info['fullname']}'")
                print(f"  Playlist file would be: {playlist_file}")
            
            # Add to platform mappings for this session
            self.platform_mappings[playlist_info['archive_name']] = playlist_info['name']
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error creating RetroArch playlist: {e}")
            return False
    
    def add_game_to_retroarch_playlist(self, platform_name: str, game: Dict, rom_path: Path) -> bool:
        """
        Add a game entry to a RetroArch playlist
        
        Args:
            platform_name: Mapped platform/playlist name
            game: Game info dictionary
            rom_path: Full path to the ROM file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.custom_systems_path or self.dest_format != 'retroarch':
            return False
        
        playlists_dir = Path(self.custom_systems_path).expanduser()
        playlist_file = playlists_dir / f"{platform_name}.lpl"
        
        if not playlist_file.exists():
            self.logger.warning(f"Playlist file does not exist: {playlist_file}")
            return False
        
        try:
            import json
            
            # Load existing playlist
            with open(playlist_file, 'r', encoding='utf-8') as f:
                playlist_data = json.load(f)
            
            # Check if game already exists in playlist
            rom_path_str = str(rom_path.resolve())
            for item in playlist_data.get('items', []):
                if item.get('path') == rom_path_str:
                    # Game already in playlist
                    return True
            
            # Create game entry
            # RetroArch playlist item structure
            game_entry = {
                "path": rom_path_str,
                "label": game['name'],
                "core_path": "DETECT",
                "core_name": "DETECT",
                "crc32": "00000000|crc",
                "db_name": f"{platform_name}.lpl"
            }
            
            # Add to playlist
            if 'items' not in playlist_data:
                playlist_data['items'] = []
            
            playlist_data['items'].append(game_entry)
            
            # Write updated playlist
            if not self.dry_run:
                with open(playlist_file, 'w', encoding='utf-8') as f:
                    json.dump(playlist_data, f, indent=2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error adding game to RetroArch playlist: {e}")
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
        
        if self.verbose:
            self.logger.info(f"Scanning games in platform: {platform_name}")
        
        platform_path = self.source / 'Games' / platform_name
        if not platform_path.exists():
            self.logger.error(f"Platform directory not found: {platform_path}")
            return []
        
        games = []
        file_count = 0
        
        # Count files first for progress
        all_items = list(platform_path.iterdir())
        total_items = len(all_items)
        
        for idx, item in enumerate(all_items, 1):
            if self.verbose and idx % 100 == 0:
                self.logger.info(f"  Scanned {idx}/{total_items} items...")
            
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
        if self.verbose:
            self.logger.info(f"  Sorting {len(games)} games...")
        games.sort(key=lambda x: x['name'].lower())
        
        if self.verbose:
            self.logger.info(f"  ✓ Found {len(games)} games in {platform_name}")
        
        self.platform_games[platform_name] = games
        return games
    
    def scan_destination_games(self, platform_name: str) -> List[Dict]:
        """
        Scan games that exist in the destination directory (for backport-only mode)
        
        Args:
            platform_name: Archive platform name
            
        Returns:
            List of game info dictionaries from destination
        """
        # Map platform name
        mapped_platform = self.map_platform_name(platform_name)
        if not mapped_platform:
            self.logger.warning(f"Cannot scan destination for unmapped platform: {platform_name}")
            return []
        
        # Determine destination platform directory
        roms_base = self.format_config['roms_path']
        if roms_base:
            platform_dest = self.destination / roms_base / mapped_platform
        else:
            platform_dest = self.destination / mapped_platform
        
        if not platform_dest.exists():
            self.logger.warning(f"Destination directory does not exist: {platform_dest}")
            return []
        
        games = []
        
        if self.verbose:
            self.logger.info(f"  Scanning destination: {platform_dest}")
        
        # Scan for game files
        for item in platform_dest.iterdir():
            # Skip metadata directories
            if item.is_dir():
                continue
            
            if item.is_file():
                game_info = {
                    'name': item.stem,  # Filename without extension
                    'filename': item.name,
                    'path': item,  # Points to destination, not archive
                    'size': item.stat().st_size,
                    'extension': item.suffix
                }
                games.append(game_info)
        
        games.sort(key=lambda x: x['name'].lower())
        
        if self.verbose:
            self.logger.info(f"  ✓ Found {len(games)} games in destination")
        
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
            'total_size': 0,
            'games_for_metadata': []  # Track all games that exist (new or skipped)
        }
        
        # Map platform name to destination format name
        mapped_platform = self.map_platform_name(platform_name)
        
        if not mapped_platform:
            # Check if it already exists as a custom system/playlist from a previous run
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
                    
            elif self.dest_format == 'retroarch':
                mapped_platform = self.check_existing_retroarch_playlist(platform_name)
                
                if mapped_platform:
                    print(f"ℹ Using existing RetroArch playlist for '{platform_name}': {mapped_platform}")
                elif not self.dry_run:
                    # Offer to add as custom playlist
                    playlist_info = self.prompt_add_retroarch_playlist(platform_name)
                    if playlist_info:
                        if self.update_retroarch_playlist(playlist_info):
                            mapped_platform = playlist_info['name']
                        else:
                            print(f"Failed to create playlist. Skipping platform: {platform_name}")
                            return stats
                    else:
                        print(f"Skipping platform: {platform_name}")
                        return stats
                else:
                    self.logger.error(f"No platform mapping for '{platform_name}' in dry-run mode")
                    print(f"✗ Skipping platform: {platform_name} (no mapping, cannot add in dry-run)")
                    return stats
                    
            else:
                self.logger.error(f"No platform mapping for '{platform_name}' and format does not support custom systems/playlists")
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
        
        for idx, game in enumerate(games, 1):
            # Show progress indicator
            if self.verbose:
                self.logger.info(f"[{idx}/{len(games)}] Processing: {game['name']}")
            
            source_path = game['path']
            dest_path = platform_dest / game['filename']
            
            if self.create_symlink(source_path, dest_path, force):
                stats['success'] += 1
                stats['total_size'] += game['size']
                stats['games_for_metadata'].append(game)  # Track for metadata export
                
                # Add to RetroArch playlist if applicable
                if self.dest_format == 'retroarch' and not self.dry_run:
                    self.add_game_to_retroarch_playlist(mapped_platform, game, dest_path)
                
                print(f"  ✓ {game['name']}")
            elif dest_path.exists() and not self.dry_run:
                stats['skipped'] += 1
                stats['games_for_metadata'].append(game)  # Also check metadata for existing games
                
                # Add to RetroArch playlist if applicable (even if game already exists)
                if self.dest_format == 'retroarch':
                    self.add_game_to_retroarch_playlist(mapped_platform, game, dest_path)
                
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
        self.logger.info(f"\n{dry_run_prefix}Checking metadata for {len(games)} games: {platform_name} -> {mapped_platform}...")
        
        # Pre-scan all metadata paths and prompt for subdirectory selections once
        self._prescan_metadata_subdirectories(platform_name)
        
        # Track unmapped directories we encounter
        unmapped_dirs = set()
        
        # Progress tracking
        total_mappings = len([m for m in metadata_mappings.items() if m[1] is not None])
        current_mapping = 0
        
        for game_idx, game in enumerate(games, 1):
            game_name = game['name']
            
            # Show game progress
            if self.verbose:
                self.logger.info(f"\n{'='*70}")
                self.logger.info(f"[Game {game_idx}/{len(games)}] {game_name}")
                self.logger.info(f"{'='*70}")
            elif game_idx % 10 == 0 or game_idx == len(games):
                # Show periodic progress for non-verbose mode
                print(f"  → Processing game {game_idx}/{len(games)}...", end='\r')
            
            # Process each metadata mapping
            for archive_path, dest_name in metadata_mappings.items():
                # Skip if destination is null (explicitly not supported)
                if dest_name is None:
                    continue
                
                if self.verbose:
                    self.logger.info(f"  Checking: {archive_path}")
                
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
                
                if self.verbose:
                    self.logger.info(f"    → Source: {metadata_base}")
                
                # Check if this metadata directory exists
                if not metadata_base.exists():
                    if archive_path not in unmapped_dirs:
                        self.logger.debug(f"Metadata directory not found: {metadata_base}")
                        unmapped_dirs.add(archive_path)
                    if self.verbose:
                        self.logger.info(f"    ✗ Directory not found")
                    continue
                
                # Check for subdirectories (e.g., regional variants like Europe, North America)
                # or architectural variants (Cocktail, Upright)
                if self.verbose:
                    self.logger.info(f"    → Checking for subdirectories...")
                
                subdirs_available = self._get_metadata_subdirectories(metadata_base)
                selected_subdirs = None
                
                if subdirs_available:
                    if self.verbose:
                        self.logger.info(f"    → Found {len(subdirs_available)} subdirectory(ies)")
                    # Prompt user to select which subdirectories to use
                    selected_subdirs = self._select_metadata_subdirectories(subdirs_available, archive_path)
                
                if self.verbose:
                    self.logger.info(f"    → Searching for files matching: {game_name}...")
                
                # Find matching files for this game (with video filtering for Videos type)
                matching_files = self._find_metadata_files(metadata_base, game_name, metadata_type, selected_subdirs)
                
                if not matching_files:
                    if self.verbose:
                        self.logger.info(f"    ✗ No matching files found")
                    continue
                
                if self.verbose:
                    self.logger.info(f"    ✓ Found {len(matching_files)} matching file(s)")
                
                # If multiple files found, let user choose (unless in non-interactive mode)
                selected_file = None
                if len(matching_files) == 1:
                    selected_file = matching_files[0]
                    if self.verbose:
                        self.logger.info(f"    → Using: {selected_file.name}")
                else:
                    if self.verbose:
                        self.logger.info(f"    → Multiple files found, prompting user...")
                    # Multiple files - need to choose one
                    selected_file = self._select_metadata_file(
                        matching_files, 
                        game_name, 
                        archive_path, 
                        dest_name
                    )
                
                if not selected_file:
                    if self.verbose:
                        self.logger.info(f"    ✗ No file selected (skipped)")
                    continue
                
                if self.verbose:
                    self.logger.info(f"    → Building destination path...")
                
                # Build destination path
                # Parse the destination name which now includes subdirectory
                # e.g., "images/box2dfront" or "videos/video"
                if '/' in dest_name:
                    # New format: "subdir/prefix"
                    dest_parts = dest_name.split('/')
                    metadata_subdir = dest_parts[0]
                    filename_prefix = dest_parts[1]
                else:
                    # Legacy format: just "prefix" - fall back to metadata_subdirs or lowercase
                    metadata_subdirs = self.format_config.get('metadata_subdirs', {})
                    metadata_subdir = metadata_subdirs.get(metadata_type, metadata_type.lower())
                    filename_prefix = dest_name
                
                # Check if metadata should be renamed to match ROM filename
                rename_to_match = self.format_config.get('rename_metadata_to_match_rom', False)
                
                if rename_to_match:
                    # ES-DE style: Use ROM filename with metadata file extension
                    # e.g., "Super Mario Bros.nes" -> "Super Mario Bros.png"
                    rom_filename = game.get('filename', game_name)  # Get actual ROM filename
                    rom_name_without_ext = Path(rom_filename).stem  # Remove ROM extension
                    dest_filename = f"{rom_name_without_ext}{selected_file.suffix}"
                    if self.verbose:
                        self.logger.info(f"    → Renaming to match ROM: {rom_filename} -> {dest_filename}")
                else:
                    # Legacy style: [gamename]-[prefix].ext
                    # e.g., "mario-box2dfront.png" or "mario-video.mp4"
                    dest_filename = f"{game_name}-{filename_prefix}{selected_file.suffix}"
                
                # Check if there's a custom metadata path (e.g., for AppImage/Flatpak)
                metadata_base_path = self.format_config.get('metadata_path')
                
                if metadata_base_path:
                    # Use custom metadata path (e.g., ~/ES-DE/downloaded_media)
                    metadata_base_expanded = Path(metadata_base_path).expanduser()
                    dest_path = metadata_base_expanded / mapped_platform / metadata_subdir / dest_filename
                elif self.format_config['metadata_subdir']:
                    # Metadata within ROM directory
                    roms_base = self.format_config.get('roms_path', '')
                    if roms_base:
                        dest_path = self.destination / roms_base / mapped_platform / metadata_subdir / dest_filename
                    else:
                        dest_path = self.destination / mapped_platform / metadata_subdir / dest_filename
                else:
                    # Separate metadata structure
                    dest_path = self.destination / 'metadata' / mapped_platform / metadata_subdir / dest_filename
                
                if self.verbose:
                    self.logger.info(f"    → Destination: {dest_path}")
                    self.logger.info(f"    → Creating {'symlink' if self.use_symlinks else 'copy'}...")
                
                # Create symlink/copy
                if self.create_symlink(selected_file, dest_path, force):
                    stats[metadata_type.lower()] += 1
                    stats['total'] += 1
                    if self.verbose:
                        self.logger.info(f"    ✓ Success")
                elif self.verbose:
                    self.logger.info(f"    ⊘ Skipped (already exists or failed)")
        
        # Clear progress line
        if not self.verbose:
            print(" " * 80, end='\r')  # Clear the progress line
        
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
    
    def _calculate_file_crc32(self, file_path: Path) -> str:
        """
        Calculate CRC32 checksum of a file
        
        Args:
            file_path: Path to the file
            
        Returns:
            CRC32 checksum as hex string
        """
        import zlib
        
        crc = 0
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(65536)  # Read in 64KB chunks
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
        
        return format(crc & 0xFFFFFFFF, '08x')
    
    def _find_next_available_filename(self, base_path: Path, base_name: str, extension: str) -> Path:
        """
        Find next available filename with incrementing suffix (_0001, _0002, etc.)
        
        Args:
            base_path: Directory where file will be placed
            base_name: Base filename without extension
            extension: File extension (including dot)
            
        Returns:
            Path with available filename
        """
        counter = 1
        while True:
            filename = f"{base_name}_{counter:04d}{extension}"
            file_path = base_path / filename
            if not file_path.exists():
                return file_path
            counter += 1
            if counter > 9999:  # Safety limit
                raise ValueError(f"Too many duplicate files for {base_name}")
    
    def backport_metadata(self, platform_name: str, games: List[Dict]) -> Dict[str, int]:
        """
        Copy metadata from destination back to master archive if it's missing in the archive
        
        Args:
            platform_name: Platform name (from master archive)
            games: List of game info dictionaries
            
        Returns:
            Dictionary with backport statistics
        """
        stats = {
            'images': 0,
            'videos': 0,
            'manuals': 0,
            'music': 0,
            'duplicates_skipped': 0,
            'renamed': 0,
            'total': 0
        }
        
        # Map platform name
        mapped_platform = self.map_platform_name(platform_name)
        if not mapped_platform:
            self.logger.warning(f"Cannot backport for unmapped platform: {platform_name}")
            return stats
        
        # Get metadata mappings from format config
        metadata_mappings = self.format_config.get('metadata_mappings', {})
        if not metadata_mappings:
            self.logger.info("No metadata mappings configured for backport")
            return stats
        
        self.logger.info(f"\n→ Checking for metadata to backport from {self.dest_format} to master archive...")
        
        backported_files = []
        duplicate_files = []
        renamed_files = []
        
        for game in games:
            game_name = game['name']
            
            for archive_path, dest_name in metadata_mappings.items():
                # Skip if destination is null (not supported)
                if dest_name is None:
                    continue
                
                # Parse paths
                path_parts = archive_path.split('/')
                metadata_type = path_parts[0]
                
                # Determine archive metadata path structure
                if len(path_parts) > 1:
                    subdir = '/'.join(path_parts[1:])
                    archive_metadata_dir = self.source / 'Metadata' / metadata_type / platform_name / subdir
                else:
                    archive_metadata_dir = self.source / 'Metadata' / metadata_type / platform_name
                
                # Parse destination path (e.g., "images/box2dfront")
                dest_parts = dest_name.split('/')
                if len(dest_parts) != 2:
                    continue
                
                metadata_subdir = dest_parts[0]
                dest_prefix = dest_parts[1]
                
                # Determine destination metadata file path
                metadata_base_path = self.format_config.get('metadata_path')
                
                if self.format_config.get('rename_metadata_to_match_rom'):
                    # Metadata uses ROM filename
                    dest_filename_base = game['filename'].rsplit('.', 1)[0]
                else:
                    # Metadata uses game name with prefix
                    dest_filename_base = f"{game_name}-{dest_prefix}"
                
                if metadata_base_path:
                    # Separate metadata location
                    metadata_base_expanded = Path(metadata_base_path).expanduser()
                    dest_metadata_dir = metadata_base_expanded / mapped_platform / metadata_subdir
                else:
                    # Metadata in ROM subdirectories
                    if self.format_config.get('metadata_subdir'):
                        roms_base = self.format_config['roms_path']
                        if roms_base:
                            platform_dest = self.destination / roms_base / mapped_platform
                        else:
                            platform_dest = self.destination / mapped_platform
                        dest_metadata_dir = platform_dest / metadata_subdir
                    else:
                        self.logger.debug(f"Unsupported metadata structure for backport")
                        continue
                
                if not dest_metadata_dir.exists():
                    continue
                
                # Look for destination metadata files
                dest_files = []
                for dest_file in dest_metadata_dir.iterdir():
                    if dest_file.is_file() and dest_file.stem == dest_filename_base:
                        dest_files.append(dest_file)
                
                if not dest_files:
                    continue
                
                # Backport the metadata file(s)
                for dest_file in dest_files:
                    # Calculate CRC of destination file
                    if not self.dry_run:
                        dest_crc = self._calculate_file_crc32(dest_file)
                    else:
                        dest_crc = "00000000"  # Placeholder for dry-run
                    
                    # Create archive directory if needed
                    if not self.dry_run:
                        archive_metadata_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Determine archive filename (use game name + original extension)
                    base_archive_filename = f"{game_name}{dest_file.suffix}"
                    archive_dest = archive_metadata_dir / base_archive_filename
                    
                    # Check for existing files with same CRC
                    duplicate_found = False
                    file_exists = archive_dest.exists()
                    
                    if file_exists or (archive_metadata_dir.exists() and not self.dry_run):
                        # Check all existing files in the archive directory for this game
                        if archive_metadata_dir.exists():
                            for existing_file in archive_metadata_dir.iterdir():
                                if not existing_file.is_file():
                                    continue
                                
                                # Check if filename starts with game name and has same extension
                                if existing_file.stem.startswith(game_name) and existing_file.suffix == dest_file.suffix:
                                    if not self.dry_run:
                                        existing_crc = self._calculate_file_crc32(existing_file)
                                        
                                        if existing_crc == dest_crc:
                                            # File with same CRC already exists - skip
                                            duplicate_found = True
                                            duplicate_files.append(dest_file.name)
                                            stats['duplicates_skipped'] += 1
                                            if self.verbose:
                                                self.logger.info(f"  ⊘ Duplicate (CRC match): {dest_file.name} = {existing_file.name}")
                                            break
                    
                    if duplicate_found:
                        continue
                    
                    # If file exists with different CRC, find next available filename
                    if file_exists and not self.dry_run:
                        # File exists but CRC is different - need to rename
                        archive_dest = self._find_next_available_filename(
                            archive_metadata_dir,
                            game_name,
                            dest_file.suffix
                        )
                        renamed_files.append(archive_dest.name)
                        stats['renamed'] += 1
                        if self.verbose:
                            self.logger.info(f"  ℹ Renamed to avoid collision: {archive_dest.name}")
                    
                    # Copy file to archive
                    if self.dry_run:
                        if file_exists:
                            self.logger.info(f"  [DRY-RUN] Would backport (renamed): {dest_file.name} → {archive_dest.name}")
                        else:
                            self.logger.info(f"  [DRY-RUN] Would backport: {dest_file.name} → {archive_dest.name}")
                        backported_files.append(archive_dest.name)
                    else:
                        try:
                            import shutil
                            shutil.copy2(dest_file, archive_dest)
                            if archive_dest.name != base_archive_filename:
                                self.logger.info(f"  ✓ Backported (renamed): {archive_dest.name}")
                            else:
                                self.logger.info(f"  ✓ Backported: {archive_dest.name}")
                            backported_files.append(archive_dest.name)
                            stats[metadata_type.lower()] += 1
                            stats['total'] += 1
                        except Exception as e:
                            self.logger.error(f"  ✗ Failed to backport {dest_file.name}: {e}")
        
        if backported_files:
            self.logger.info(f"\n✓ Backported {len(backported_files)} metadata file(s) to master archive")
            if renamed_files:
                self.logger.info(f"  ℹ {len(renamed_files)} file(s) renamed to prevent collisions")
            if duplicate_files:
                self.logger.info(f"  ⊘ {len(duplicate_files)} duplicate(s) skipped (same CRC)")
        else:
            if duplicate_files:
                self.logger.info(f"  ⊘ All files already exist (skipped {len(duplicate_files)} duplicate(s))")
            else:
                self.logger.info(f"  No new metadata found to backport")
        
        return stats
    
    def _is_video_file(self, file_path: Path) -> bool:
        """
        Check if a file is a video format
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if file is a video format, False otherwise
        """
        # Common video file extensions
        video_extensions = {
            '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm',
            '.m4v', '.mpg', '.mpeg', '.3gp', '.ogv', '.m2ts', '.mts',
            '.vob', '.divx', '.xvid', '.f4v', '.rm', '.rmvb', '.asf'
        }
        
        return file_path.suffix.lower() in video_extensions
    
    def _get_metadata_subdirectories(self, base_path: Path) -> List[str]:
        """
        Get list of subdirectories under a metadata path
        
        Args:
            base_path: Base metadata directory to check
            
        Returns:
            List of subdirectory names
        """
        subdirs = []
        
        if not base_path.exists():
            return subdirs
        
        for item in base_path.iterdir():
            if item.is_dir():
                subdirs.append(item.name)
        
        return sorted(subdirs)
    
    def _scan_all_metadata_subdirectories(self) -> None:
        """
        Scan ALL platforms and ALL metadata paths to build a comprehensive list of subdirectories.
        Prompt the user ONCE to select which subdirectories to use globally across all platforms.
        This ensures the user only needs to make one selection (e.g., "North America, Europe, Japan")
        and those selections will be applied everywhere.
        """
        if self.metadata_subdirs_scanned:
            # Already scanned
            return
        
        if self.dry_run or self.auto_select_metadata:
            # In dry-run or auto-select mode, skip scanning and use all subdirectories
            self.global_metadata_subdirs = None  # Will use all subdirs
            self.metadata_subdirs_scanned = True
            return
        
        metadata_mappings = self.format_config.get('metadata_mappings', {})
        if not metadata_mappings:
            self.metadata_subdirs_scanned = True
            return
        
        self.logger.info(f"\nScanning all platforms for metadata subdirectories...")
        
        # Collect ALL unique subdirectories across all platforms and metadata types
        all_subdirs = set()
        platforms_checked = 0
        
        # Get all available platforms from the Games directory
        games_dir = self.source / 'Games'
        if not games_dir.exists():
            self.metadata_subdirs_scanned = True
            return
        
        platforms = [p.name for p in games_dir.iterdir() if p.is_dir()]
        
        for platform_name in platforms:
            platforms_checked += 1
            if platforms_checked % 10 == 0:
                print(f"  Scanning platform {platforms_checked}/{len(platforms)}...", end='\r')
            
            for archive_path, dest_name in metadata_mappings.items():
                # Skip if destination is null
                if dest_name is None:
                    continue
                
                # Parse archive path (e.g., "Images/Box - Front" or "Videos")
                path_parts = archive_path.split('/')
                metadata_type = path_parts[0]
                
                # Build the source path
                if len(path_parts) > 1:
                    subdir = '/'.join(path_parts[1:])
                    metadata_base = self.source / 'Metadata' / metadata_type / platform_name / subdir
                else:
                    metadata_base = self.source / 'Metadata' / metadata_type / platform_name
                
                # Check if directory exists
                if not metadata_base.exists():
                    continue
                
                # Get subdirectories and add to global set
                subdirs = self._get_metadata_subdirectories(metadata_base)
                all_subdirs.update(subdirs)
        
        print()  # Clear the progress line
        
        # If we found subdirectories, prompt user to select them globally
        if all_subdirs:
            subdirs_sorted = sorted(all_subdirs)
            
            print(f"\n{'='*70}")
            print(f"GLOBAL METADATA SUBDIRECTORY SELECTION")
            print(f"{'='*70}")
            print(f"Found {len(subdirs_sorted)} unique subdirectory(ies) across all platforms:")
            for i, subdir in enumerate(subdirs_sorted, 1):
                print(f"  {i}. {subdir}")
            
            print(f"\nThese subdirectories contain regional, language, or variant metadata.")
            print(f"Select which ones to use - your selection will be applied to")
            print(f"ALL platforms and ALL metadata types throughout the export.")
            print(f"\nOptions:")
            print(f"  Enter numbers (comma-separated) to select specific subdirectories")
            print(f"  a - Select all subdirectories")
            print(f"  n - Skip subdirectories (search base directories only)")
            print(f"{'='*70}")
            
            while True:
                choice = input(f"\nSelect subdirectories [1-{len(subdirs_sorted)}/a/n]: ").strip().lower()
                
                if choice == 'a':
                    self.global_metadata_subdirs = subdirs_sorted
                    print(f"✓ Selected all {len(subdirs_sorted)} subdirectories")
                    break
                elif choice == 'n':
                    self.global_metadata_subdirs = []
                    print(f"✓ Will search base directories only (no subdirectories)")
                    break
                elif choice:
                    try:
                        # Parse comma-separated numbers
                        selected_indices = [int(x.strip()) - 1 for x in choice.split(',')]
                        selected = [subdirs_sorted[i] for i in selected_indices if 0 <= i < len(subdirs_sorted)]
                        
                        if selected:
                            self.global_metadata_subdirs = selected
                            print(f"✓ Selected {len(selected)} subdirectory(ies): {', '.join(selected)}")
                            break
                        else:
                            print("Invalid selection. Please try again.")
                    except (ValueError, IndexError):
                        print("Invalid input. Please enter numbers separated by commas, or 'a' for all, 'n' for none.")
                else:
                    print("Please make a selection.")
            
            print(f"\n✓ Global subdirectory selection complete. These will be used for all platforms.\n")
        else:
            self.logger.info(f"No metadata subdirectories found in archive")
            self.global_metadata_subdirs = []
        
        self.metadata_subdirs_scanned = True
    
    def _prescan_metadata_subdirectories(self, platform_name: str) -> None:
        """
        Ensure the global metadata subdirectory scan has been performed.
        The actual scanning is done once across all platforms by _scan_all_metadata_subdirectories.
        
        Args:
            platform_name: Platform name (from master archive) - kept for compatibility
        """
        # The global scan is now done once in export_games before any platform processing
        # This method is kept for compatibility but no longer does platform-specific scanning
        pass
    
    def _select_metadata_subdirectories(self, subdirs: List[str], archive_path: str) -> List[str]:
        """
        Filter available subdirectories based on global user selection.
        
        Args:
            subdirs: List of available subdirectories in this specific path
            archive_path: Archive metadata path (for caching)
            
        Returns:
            List of selected subdirectory names that exist in this path
        """
        # Check cache first
        if archive_path in self.metadata_subdir_cache:
            return self.metadata_subdir_cache[archive_path]
        
        if not subdirs:
            self.metadata_subdir_cache[archive_path] = []
            return []
        
        if self.dry_run or self.auto_select_metadata:
            # In dry-run or auto-select mode, use all subdirectories
            self.metadata_subdir_cache[archive_path] = subdirs
            return subdirs
        
        # Use global selection if available
        if self.global_metadata_subdirs is None:
            # No global selection made (shouldn't happen, but fallback to all)
            selected = subdirs
        elif not self.global_metadata_subdirs:
            # User chose to skip subdirectories globally
            selected = []
        else:
            # Filter subdirs to only those in the global selection
            selected = [s for s in subdirs if s in self.global_metadata_subdirs]
        
        self.metadata_subdir_cache[archive_path] = selected
        return selected
    
    def _find_metadata_files(self, base_path: Path, game_name: str, metadata_type: str = None, 
                            subdirs: List[str] = None) -> List[Path]:
        """
        Find metadata files matching the game name
        
        Args:
            base_path: Base directory to search
            game_name: Game name to match
            metadata_type: Type of metadata (e.g., "Videos") for format filtering
            subdirs: Optional list of subdirectories to search within base_path
            
        Returns:
            List of matching file paths
        """
        matching_files = []
        
        if not base_path.exists():
            return matching_files
        
        # Determine which directories to search
        search_paths = []
        if subdirs:
            # Search specified subdirectories
            for subdir in subdirs:
                subdir_path = base_path / subdir
                if subdir_path.exists() and subdir_path.is_dir():
                    search_paths.append(subdir_path)
        else:
            # Search base directory only
            search_paths.append(base_path)
        
        # Search for files in the determined paths
        for search_path in search_paths:
            for file_path in search_path.iterdir():
                if file_path.is_file():
                    # Check if filename starts with game name
                    if file_path.stem.startswith(game_name):
                        # For Videos metadata, only include actual video files
                        if metadata_type == "Videos":
                            if self._is_video_file(file_path):
                                matching_files.append(file_path)
                            else:
                                self.logger.debug(
                                    f"Skipping non-video file in Videos directory: {file_path.name}"
                                )
                        else:
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
    
    def load_xml_metadata(self, xml_path: str) -> None:
        """
        Load game metadata from XML file (e.g., LaunchBox Metadata.xml)
        
        Args:
            xml_path: Path to the XML metadata file
        """
        xml_file = Path(xml_path).expanduser()
        
        if not xml_file.exists():
            self.logger.error(f"XML metadata file not found: {xml_file}")
            return
        
        self.logger.info(f"Loading XML metadata from: {xml_file}")
        
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(xml_file)
            root = tree.getroot()
            
            # Parse games from XML
            games_parsed = 0
            for game_elem in root.findall('.//Game'):
                # Extract game data
                game_data = {}
                for child in game_elem:
                    game_data[child.tag] = child.text if child.text else ""
                
                # Get platform and game name
                platform = game_data.get('Platform', '')
                name = game_data.get('Name', '')
                
                if not platform or not name:
                    continue
                
                # Initialize platform dict if needed
                if platform not in self.xml_metadata:
                    self.xml_metadata[platform] = {}
                
                # Store game metadata
                self.xml_metadata[platform][name] = game_data
                games_parsed += 1
            
            self.logger.info(f"✓ Loaded metadata for {games_parsed} games across {len(self.xml_metadata)} platforms")
            
        except Exception as e:
            self.logger.error(f"Error parsing XML metadata: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
    
    def export_gamelist_xml(self, platform_name: str, games: List[Dict]) -> bool:
        """
        Export gamelist.xml file for a platform with game metadata
        
        Args:
            platform_name: Platform name (from master archive)
            games: List of game info dictionaries
            
        Returns:
            True if successful, False otherwise
        """
        # Check if format supports gamelist XML
        gamelist_path = self.format_config.get('gamelist_path')
        xml_mappings = self.format_config.get('xml_metadata_mappings', {})
        xml_conversions = self.format_config.get('xml_field_conversions', {})
        
        if not gamelist_path:
            self.logger.info(f"✗ Format {self.dest_format} does not support gamelist XML (no gamelist_path configured)")
            return False
        
        # Map platform name for directory structure
        mapped_platform = self.map_platform_name(platform_name)
        if not mapped_platform:
            self.logger.warning(f"✗ Could not map platform name: {platform_name}")
            return False
        
        # Look for metadata using the original archive platform name
        # The XML metadata uses the original LaunchBox/archive platform names
        platform_metadata = {}
        has_xml_metadata = False
        
        if self.xml_metadata and platform_name in self.xml_metadata:
            platform_metadata = self.xml_metadata[platform_name]
            has_xml_metadata = True
            if self.verbose:
                self.logger.info(f"  Found {len(platform_metadata)} games in XML metadata for {platform_name}")
        else:
            # No XML metadata for this platform - will create basic gamelist with just path and name
            if self.xml_metadata:
                self.logger.info(f"  No XML metadata found for platform '{platform_name}' - creating basic gamelist")
            else:
                self.logger.info(f"  No XML metadata loaded - creating basic gamelist with path and name only")
        
        
        # Build gamelist XML path
        gamelist_base = Path(gamelist_path).expanduser()
        gamelist_dir = gamelist_base / mapped_platform
        gamelist_file = gamelist_dir / 'gamelist.xml'
        
        if self.verbose:
            self.logger.info(f"  Gamelist path: {gamelist_file}")
        
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would create gamelist.xml at: {gamelist_file}")
            if has_xml_metadata:
                matched = sum(1 for g in games if g['name'] in platform_metadata)
                self.logger.info(f"[DRY RUN] Would include {len(games)} games ({matched} with metadata, {len(games)-matched} basic entries)")
            else:
                self.logger.info(f"[DRY RUN] Would include {len(games)} games with basic entries (path and name only)")
            return True
        
        try:
            # Create directory if needed
            gamelist_dir.mkdir(parents=True, exist_ok=True)
            
            if self.verbose:
                self.logger.info(f"  Created directory: {gamelist_dir}")
            
            # Build XML structure
            import xml.etree.ElementTree as ET
            from xml.dom import minidom
            
            root = ET.Element('gameList')
            
            games_with_metadata = 0
            games_basic_only = 0
            
            for game in games:
                game_name = game['name']
                
                # Create game element (always create, even without metadata)
                game_elem = ET.SubElement(root, 'game')
                
                # Always add path element (relative to ROM directory)
                path_elem = ET.SubElement(game_elem, 'path')
                path_elem.text = f"./{game['filename']}"
                
                # Always add name element
                name_elem = ET.SubElement(game_elem, 'name')
                name_elem.text = game_name
                
                # Check if we have additional metadata for this game
                if has_xml_metadata and game_name in platform_metadata:
                    source_data = platform_metadata[game_name]
                    
                    # Process all fields from source XML (except Name and Platform which we already handled)
                    for source_field, source_value in source_data.items():
                        if not source_value:
                            continue
                        
                        # Skip fields we've already handled or don't want
                        if source_field in ['Name', 'Platform']:
                            continue
                        
                        # Determine destination field name (mapped or pass-through)
                        dest_field = xml_mappings.get(source_field, source_field.lower())
                        
                        # Skip if it would duplicate the name field
                        if dest_field == 'name':
                            continue
                        
                        # Apply conversions if configured
                        converted_value = self._apply_xml_field_conversion(
                            source_field, 
                            source_value, 
                            xml_conversions
                        )
                        
                        if converted_value:
                            field_elem = ET.SubElement(game_elem, dest_field)
                            field_elem.text = str(converted_value)
                    
                    games_with_metadata += 1
                else:
                    games_basic_only += 1
            
            # Always write the gamelist, even if no metadata
            total_games = games_with_metadata + games_basic_only
            # Always write the gamelist, even if no metadata
            total_games = games_with_metadata + games_basic_only
            
            # Write XML file with pretty formatting
            xml_string = ET.tostring(root, encoding='unicode')
            dom = minidom.parseString(xml_string)
            pretty_xml = dom.toprettyxml(indent='  ')
            
            # Remove extra blank lines
            pretty_xml = '\n'.join([line for line in pretty_xml.split('\n') if line.strip()])
            
            with open(gamelist_file, 'w', encoding='utf-8') as f:
                f.write(pretty_xml)
            
            self.logger.info(f"✓ Created gamelist.xml: {gamelist_file}")
            self.logger.info(f"  Total games: {total_games}")
            if games_with_metadata > 0:
                self.logger.info(f"  With metadata: {games_with_metadata}")
            if games_basic_only > 0:
                self.logger.info(f"  Basic entries (path/name only): {games_basic_only}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error creating gamelist.xml for {platform_name}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def _apply_xml_field_conversion(self, field_name: str, value: str, conversions: Dict) -> str:
        """
        Apply configured conversions to XML field values
        
        Args:
            field_name: Name of the source field
            value: Original value from source XML
            conversions: Dictionary of conversion configurations
            
        Returns:
            Converted value as string
        """
        if field_name not in conversions:
            return value
        
        conversion = conversions[field_name]
        conversion_type = conversion.get('type')
        
        try:
            if conversion_type == 'date':
                # Date format conversion (e.g., year to full date)
                format_str = conversion.get('format', '{year}')
                
                # Extract year, month, day if present in value
                year = value.strip()
                month = conversion.get('default_month', '01')
                day = conversion.get('default_day', '01')
                
                # Format the output
                result = format_str.format(year=year, month=month, day=day)
                return result
                
            elif conversion_type == 'normalize':
                # Normalize numeric values (e.g., rating scales)
                source_scale = float(conversion.get('source_scale', 1.0))
                target_scale = float(conversion.get('target_scale', 1.0))
                decimal_places = int(conversion.get('decimal_places', 2))
                
                # Convert value
                numeric_value = float(value)
                normalized = (numeric_value / source_scale) * target_scale
                
                # Format with specified decimal places
                return f"{normalized:.{decimal_places}f}"
                
            else:
                # Unknown conversion type, return original
                return value
                
        except Exception as e:
            self.logger.warning(f"Error converting field {field_name}: {e}, using original value")
            return value
    
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
        '--backport',
        action='store_true',
        help='Copy metadata found in destination back to master archive if missing (builds up archive metadata over time)'
    )
    
    parser.add_argument(
        '--backport-only',
        action='store_true',
        help='Only perform backport operation (skip game and metadata export). Useful for refreshing archive after scraping in frontend.'
    )
    
    parser.add_argument(
        '--infoxml',
        metavar='PATH',
        help='Path to XML file with game metadata (e.g., LaunchBox Metadata.xml) to import into gamelist.xml'
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
            use_symlinks=args.symlink,
            backport=args.backport
        )
        
        print("\n" + "=" * 70)
        print("MASTER ARCHIVE EXPORT TOOL")
        if args.dry_run:
            print("*** DRY RUN MODE - NO FILES WILL BE CREATED ***")
        if args.backport_only:
            print("*** BACKPORT-ONLY MODE - SKIP EXPORT, BACKPORT METADATA ONLY ***")
        elif args.backport:
            print("*** BACKPORT MODE - COPYING METADATA TO ARCHIVE ***")
        print("=" * 70)
        print(f"Format: {exporter.format_config['name']}")
        print(f"Destination: {exporter.destination}")
        if not args.backport_only:
            print(f"Mode: {'Symlinks' if args.symlink else 'Copy files'}")
        if args.verbose:
            print("Verbose logging: ENABLED")
        if args.backport_only:
            print("Backport-only mode: ENABLED (scanning destination, no export)")
        elif args.backport:
            print("Backport mode: ENABLED (will copy metadata from destination to archive)")
        
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
        
        # Load XML metadata if provided
        if args.infoxml:
            exporter.load_xml_metadata(args.infoxml)
        
        # Handle backport-only mode
        if args.backport_only:
            print("\n→ BACKPORT-ONLY MODE: Scanning destination and backporting metadata...")
            
            all_backport_stats = {}
            
            for platform in platforms_to_export:
                print(f"\n{'='*70}")
                print(f"Platform: {platform}")
                print(f"{'='*70}")
                
                # Scan games from destination directory
                games_in_dest = exporter.scan_destination_games(platform)
                
                if not games_in_dest:
                    print(f"No games found in destination for {platform}. Skipping.")
                    continue
                
                print(f"Found {len(games_in_dest)} games in destination")
                
                # Backport metadata for these games
                backport_stats = exporter.backport_metadata(platform, games_in_dest)
                all_backport_stats[platform] = backport_stats
                
                if backport_stats['total'] > 0:
                    print(f"\nMetadata backported to master archive:")
                    for mtype, count in backport_stats.items():
                        if count > 0 and mtype != 'total':
                            print(f"  {mtype}: {count} files")
                else:
                    print("  No new metadata found to backport")
            
            # Print summary
            if all_backport_stats:
                total_backported = sum(stats['total'] for stats in all_backport_stats.values())
                print(f"\n{'='*70}")
                print("BACKPORT SUMMARY")
                print(f"{'='*70}")
                print(f"Total files backported: {total_backported}")
                for platform, stats in all_backport_stats.items():
                    if stats['total'] > 0:
                        print(f"\n{platform}:")
                        for mtype, count in stats.items():
                            if count > 0 and mtype != 'total':
                                print(f"  {mtype}: {count} files")
            
            if args.dry_run:
                print("\n✓ Backport-only dry run completed!")
            else:
                print("\n✓ Backport-only completed successfully!")
            return 0
        
        # Perform global metadata subdirectory scan before processing any platforms
        # This ensures the user only needs to make one selection for all platforms
        if platforms_to_export and not args.no_metadata:
            exporter._scan_all_metadata_subdirectories()
        
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
            # Check metadata for all games that exist (newly exported or already existing)
            if not args.no_metadata and stats['games_for_metadata']:
                games_count = len(stats['games_for_metadata'])
                if stats['skipped'] > 0:
                    print(f"\n→ Checking metadata for {games_count} games (including {stats['skipped']} already existing)...")
                
                metadata_stats = exporter.export_metadata(
                    platform, 
                    stats['games_for_metadata'],  # Use tracked games instead of all selected
                    metadata_types=args.metadata_types,
                    force=args.force
                )
                print(f"\nMetadata exported:")
                for mtype, count in metadata_stats.items():
                    if count > 0 and mtype != 'total':
                        print(f"  {mtype}: {count} files")
            
            # Backport metadata from destination to archive if requested
            if args.backport and stats['games_for_metadata']:
                backport_stats = exporter.backport_metadata(platform, stats['games_for_metadata'])
                if backport_stats['total'] > 0:
                    print(f"\nMetadata backported to master archive:")
                    for mtype, count in backport_stats.items():
                        if count > 0 and mtype != 'total':
                            print(f"  {mtype}: {count} files")
            
            # Export gamelist.xml if format supports it
            # Will create basic gamelist (path/name) even without XML metadata
            # If --infoxml was provided, will include additional metadata fields
            if stats['games_for_metadata']:
                gamelist_path = exporter.format_config.get('gamelist_path')
                if gamelist_path:
                    print(f"\n→ Generating gamelist.xml for {platform}...")
                    exporter.export_gamelist_xml(platform, stats['games_for_metadata'])
        
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
            if exporter.dest_format in ['es-de', 'retroarch']:
                system_type = "custom systems" if exporter.dest_format == 'es-de' else "playlists"
                print(f"  2. Or run without --dry-run to interactively add as {system_type}")
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
