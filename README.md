# Master Archive Export Tool

Export games and metadata from a master archive (NFS mount) to various emulation frontend destinations using symlinks.

## Features

- **Multiple Frontend Support**: Configurable formats via JSON (currently ES-DE)
- **Platform Mapping**: Automatic translation from Master Archive names to frontend-specific directory names
- **Custom System Support**: Automatically prompt to add unmapped platforms to ES-DE custom systems
- **Interactive Mode**: Step through platforms and games with y/n/a/q options
- **Fuzzy Matching**: Find platforms and games by partial name
- **Symlink or Copy**: Choose to create symlinks (saves space) or copy files (portable)
- **Dry-run Mode**: Preview what would be exported without creating files
- **Verbose Logging**: Detailed logging for troubleshooting
- **Format Defaults**: Each frontend has its own default installation path
- **Path Validation**: Automatically validates and creates required directories with detailed error messages

## Requirements

- Python 3.6 or higher
- Read access to master archive (typically NFS-mounted)
- Write access to destination frontend directory

### Linux Users: Virtual Environment Setup

If you encounter missing module errors on Linux, it's recommended to use a virtual environment:

```bash
# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install any missing dependencies (if needed)
# pip install <module-name>

# Run the script
python init.py --help

# When done, deactivate the virtual environment
deactivate
```

**Note:** Python 3 typically includes all required modules (`pathlib`, `argparse`, `logging`, `json`, `xml.etree.ElementTree`, `difflib`, `shutil`, `os`) in the standard library, so no additional installations should be necessary.

## Installation

```bash
# Clone or download the script
cd /path/to/script

# Ensure fe_formats.json is in the same directory as init.py
```

## Usage

### Basic Commands

```bash
# List available formats
python init.py --list-formats

# Show platform mappings for a format
python init.py --show-mappings es-de

# Use format defaults (ES-DE: ~/.emulationstation/ROMs)
python init.py --platform "nes" --games ALL

# Interactive mode (choose platform and games)
python init.py

# Override destination directory
python init.py --dest /home/user/games --platform "snes" --games ALL

# Dry run - preview without creating files
python init.py --dry-run --platform ALL --games ALL

# Verbose mode - detailed logging
python init.py --verbose --platform "genesis" --games INTERACTIVE

# Copy files instead of symlinks
python init.py --symlink false --platform "nes" --games ALL

# Dry run with copy mode to see actual disk space needed
python init.py --symlink false --dry-run --platform ALL --games ALL
```

### Platform Selection Options

- `--platform ALL` - Export all platforms
- `--platform INTERACTIVE` - Step through each platform (y/n/a/q)
- `--platform "name"` - Fuzzy match platform name (e.g., "nes", "snes")
- No `--platform` - Interactive menu to select one platform

### Game Selection Options

- `--games ALL` - Export all games
- `--games INTERACTIVE` - Step through each game (y/n/a/q)
- `--games "name"` - Fuzzy match game name (e.g., "sonic", "mega man")
- No `--games` - Defaults to ALL

### Interactive Controls

When using `INTERACTIVE` mode:
- `y/yes` - Include this item
- `n/no` - Skip this item
- `a/all` - Include all remaining items
- `q/quit` - Stop and process selected items

## Adding New Formats

To add support for a new frontend/emulator, edit `fe_formats.json`:

```json
{
  "formats": {
    "your-format-id": {
      "name": "Display Name",
      "default_destination": "~/path/to/default/location",
      "roms_path": "subdirectory_for_roms",
      "metadata_subdir": true,
      "description": "Brief description",
      "platforms_subdir": true,
      "custom_systems_path": "~/path/to/custom/systems.xml",
      "platform_mappings": {
        "Master Archive Name": "destination_name",
        "Nintendo Entertainment System": "nes"
      }
    }
  }
}
```

### Format Configuration Fields

- **name**: Display name shown to users
- **default_destination**: Default installation path (~ expands to home)
- **roms_path**: Subdirectory name for ROMs within destination
- **metadata_subdir**: `true` = metadata in ROM folders, `false` = separate structure
- **platforms_subdir**: `true` = ROMs organized by platform subdirectories
- **description**: Brief description of the format
- **custom_systems_path**: Path to custom systems XML file (for adding unmapped platforms)
- **platform_mappings**: Dictionary mapping Master Archive platform names to destination directory names

### Example: Adding RetroBat Support

```json
{
  "formats": {
    "es-de": {
      "name": "ES-DE (EmulationStation Desktop Edition)",
      "default_destination": "~/.emulationstation/ROMs",
      "roms_path": "ROMs",
      "metadata_subdir": true,
      "description": "EmulationStation Desktop Edition format",
      "platforms_subdir": true
    },
    "retrobat": {
      "name": "RetroBat",
      "default_destination": "~/retrobat/roms",
      "roms_path": "",
      "metadata_subdir": false,
      "description": "RetroBat frontend format",
      "platforms_subdir": true
    }
  }
}
```

Then use it with:
```bash
python init.py --format retrobat --platform "nes" --games ALL
```

## Platform Mapping

The script automatically maps Master Archive platform names to frontend-specific directory names using the `platform_mappings` in `fe_formats.json`.

### Viewing Platform Mappings

```bash
# See all platform mappings for ES-DE
python init.py --show-mappings es-de
```

### Handling Unmapped Platforms

When a platform from the Master Archive doesn't have a mapping:

1. **Dry-run mode**: The platform is skipped and listed in the unmapped platforms summary. If you choose to add it as a custom system, you'll see a preview of the XML that would be written.

2. **Normal mode (ES-DE)**: You'll be prompted to add it as a custom system:
   ```
   UNMAPPED PLATFORM: Custom Platform Name
   ======================================================================
   This platform is not mapped in the es-de configuration.
   You can add it as a custom system to ES-DE.
   
   Add as custom system? (y/n): y
   
   Enter system information:
   System name [customplatform]: 
   Full name [Custom Platform Name]: 
   Extensions [.zip,.7z]: 
   Emulator command [retroarch]: 
   RetroArch core (optional): 
   
   ======================================================================
   CUSTOM SYSTEM XML TO BE ADDED:
   ======================================================================
     <system>
       <name>customplatform</name>
       <fullname>Custom Platform Name</fullname>
       <path>./roms/customplatform</path>
       <extension>.zip,.7z</extension>
       <command>retroarch %ROM%</command>
       <platform>customplatform</platform>
       <theme>customplatform</theme>
     </system>
   ======================================================================
   
   Target file: /home/user/.emulationstation/custom_systems/es_systems.xml
   ```

The script will automatically:
- Show you exactly what XML will be added to the custom systems file
- Create/update `~/.emulationstation/custom_systems/es_systems.xml`
- Add the platform mapping for the current session
- Create the appropriate directory structure

### Adding Platform Mappings Manually

Edit `fe_formats.json` and add entries to `platform_mappings`:

```json
"platform_mappings": {
  "Nintendo Entertainment System": "nes",
  "Super Nintendo Entertainment System": "snes",
  "Your Custom Platform": "customplatform"
}
```

## Master Archive Structure

The script expects the following archive structure:

```
/mnt/Emulators/Master Archive/
├── Games/
│   ├── Nintendo Entertainment System/
│   │   ├── Game1.nes
│   │   ├── Game2.7z
│   │   └── ...
│   ├── Super Nintendo Entertainment System/
│   └── [other platforms]/
│
└── Metadata/
    ├── Images/
    │   ├── Nintendo Entertainment System/
    │   │   ├── Box - Front/
    │   │   ├── Box - Back/
    │   │   ├── Screenshot - Gameplay/
    │   │   └── ...
    │   └── [other platforms]/
    │
    ├── Videos/
    │   └── [platforms]/
    │
    ├── Manuals/
    └── Music/
```

## Command-Line Options

```
positional arguments:
  source                Path to master archive (default: /mnt/Emulators/Master Archive)
  destination           Destination directory (optional - uses format default)

optional arguments:
  --dest, --override-destination
                        Override destination directory
  --format              Destination format (default: es-de)
  --list-formats        List all available destination formats
  --show-mappings       Show platform mappings for specified format
  --platform            Platform selection (ALL, INTERACTIVE, or name)
  --games               Game selection (ALL, INTERACTIVE, or name)
  --force               Force overwrite existing files
  --no-metadata         Skip exporting metadata files
  --metadata-types      Types to export (Images, Videos, Manuals, Music)
  --config              Path to configuration file
  --verbose, -v         Enable verbose output
  --dry-run             Simulate without creating symlinks
```

## Examples

```bash
# List available formats
python init.py --list-formats

# Interactive: choose everything
python init.py

# Export all NES games with default destination
python init.py --platform "nes" --games ALL

# Interactive game selection for SNES
python init.py --platform "snes" --games INTERACTIVE

# Find and export Sonic games from Genesis
python init.py --platform "genesis" --games "sonic"

# Export all platforms interactively
python init.py --platform INTERACTIVE --games ALL

# Dry run to preview
python init.py --dry-run --platform ALL --games ALL

# Custom destination with verbose logging
python init.py --dest /custom/path --verbose --platform "nes" --games ALL
```

## Notes

- Symlinks save disk space but require the master archive to remain mounted
- Use `--dry-run` to preview before committing
- Fuzzy matching helps find platforms/games without exact names
- Format defaults make it easy to export to standard locations
- The `fe_formats.json` file can be extended with new frontend formats

## Path Validation and Error Handling

The script performs comprehensive validation of all paths and directories:

### Source Archive Validation

The script validates that the master archive has the correct structure:
- Checks that the source path exists and is accessible
- Verifies the `Games/` directory exists (required)
- Warns if the `Metadata/` directory is missing (optional)

**Example error:**
```
ValueError: Invalid master archive structure: 'Games' directory not found at /mnt/Emulators/Master Archive/Games
Expected structure: /mnt/Emulators/Master Archive/Games/[Platform]/[games]
```

### Destination Directory Creation

The script automatically creates destination directories:
- Creates the main destination directory if it doesn't exist
- Creates custom systems directories for ES-DE if configured
- Creates template XML files when needed
- In dry-run mode, shows what would be created without making changes

**Example error:**
```
ValueError: Permission denied when creating destination directory: /home/user/.emulationstation/ROMs
Error: [Errno 13] Permission denied: '/home/user/.emulationstation/ROMs'
Please check directory permissions or run with appropriate privileges
```

### Format Configuration Validation

The script validates the `fe_formats.json` file:
- Ensures all required fields are present (`name`, `default_destination`, `description`)
- Validates that paths are not empty
- Checks JSON syntax is correct

**Example error:**
```
ValueError: Format 'es-de' is missing required fields: default_destination
Required fields: name, default_destination, description
```

### Troubleshooting

If you encounter errors:

1. **Permission Denied**: Run with appropriate privileges or check directory ownership
   ```bash
   # Check directory permissions
   ls -ld /path/to/directory
   
   # Create directory manually with correct permissions
   mkdir -p /path/to/directory
   chmod 755 /path/to/directory
   ```

2. **Source Not Found**: Ensure the NFS mount is connected
   ```bash
   # Check if mount point exists
   mount | grep Emulators
   
   # Remount if necessary
   sudo mount -t nfs 192.168.1.3:/Emulators /mnt/Emulators
   ```

3. **Invalid Archive Structure**: Verify the master archive layout matches expectations
   ```bash
   # Check structure
   ls -la "/mnt/Emulators/Master Archive/"
   # Should show: Games/ and Metadata/ directories
   ```

4. **Configuration Errors**: Validate your `fe_formats.json` file
   ```bash
   # Test JSON syntax
   python -m json.tool fe_formats.json
   ```
