# Master Archive Export Tool

Export games and metadata from a master archive (NFS mount) to various emulation frontend destinations using symlinks.

## Credits

**Code Captain**: Idgarad Lyracante  
**Primary AI Developer**: GitHub Copilot (Claude 3.5 Sonnet)

This project was developed through an AI-human collaboration, combining domain expertise with advanced code generation capabilities.

## Features

- **Multiple Frontend Support**: Configurable formats via JSON (currently ES-DE)
- **Platform Mapping**: Automatic translation from Master Archive names to frontend-specific directory names
- **Custom System Support**: Automatically prompt to add unmapped platforms to ES-DE custom systems
- **Interactive Mode**: Step through platforms and games with y/n/a/q options
- **Fuzzy Matching**: Find platforms and games by partial name
- **Symlink or Copy**: Choose to create symlinks (saves space) or copy files (portable)
- **Smart Metadata Export**: Automatically checks and exports missing metadata even for games that already exist
- **Dry-run Mode**: Preview what would be exported without creating files
- **Progress Indicators**: Shows real-time progress during long operations with periodic status updates
- **Verbose Logging**: Detailed task-by-task logging with full paths and operation status for troubleshooting
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

# Verbose mode - detailed logging with full file paths
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
      "metadata_path": null,
      "metadata_subdir": true,
      "rename_metadata_to_match_rom": false,
      "description": "Brief description",
      "platforms_subdir": true,
      "custom_systems_path": "~/path/to/custom/systems.xml",
      "metadata_mappings": {
        "Images/Box - Front": "images/box2dfront",
        "Images/Box - Back": "images/box2dback",
        "Images/Screenshot - Gameplay": "images/screenshot",
        "Videos": "videos/video",
        "Manuals": "manuals/manual",
        "Music": null
      },
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
- **roms_path**: Subdirectory name for ROMs within destination (e.g., `"ROMs"`)
- **metadata_path**: Optional override path for metadata location (e.g., `"~/ES-DE/downloaded_media"` for AppImage/Flatpak). If `null`, metadata is stored relative to `default_destination` based on `metadata_subdir` setting.
- **metadata_subdir**: `true` = metadata in ROM folders, `false` = separate structure
- **rename_metadata_to_match_rom**: `true` = metadata files use ROM filename (ES-DE requirement), `false` = use game name with prefix (see below)
- **platforms_subdir**: `true` = ROMs organized by platform subdirectories
- **description**: Brief description of the format
- **custom_systems_path**: Path to custom systems XML file (for adding unmapped platforms)
- **platform_mappings**: Dictionary mapping Master Archive platform names to destination directory names
- **metadata_mappings**: Dictionary mapping Master Archive metadata paths to destination paths with format `"subdirectory/prefix"` (see below)

### Metadata Mappings

The `metadata_mappings` field controls which metadata from the Master Archive gets exported and how it's organized in the destination format. This is crucial because different frontends support different metadata types and naming conventions.

**Format**: `"Archive/Path": "subdirectory/prefix"`

The destination value uses the format `"subdirectory/prefix"` where:
- **subdirectory**: The metadata subdirectory (e.g., `images`, `videos`, `manuals`)
- **prefix**: The filename prefix or type identifier (e.g., `box2dfront`, `video`, `manual`)

**Archive Path Examples**:
- `"Images/Box - Front"` - Specific image type from Master Archive
- `"Images/Screenshot - Gameplay"` - Gameplay screenshots
- `"Videos"` - All videos for the platform
- `"Manuals"` - Game manuals
- `"Music"` - Background music

**Destination Format**:
- `"images/box2dfront"` - Files go in `images/` subdirectory with `box2dfront` prefix
- `"videos/video"` - Files go in `videos/` subdirectory with `video` prefix
- `"manuals/manual"` - Files go in `manuals/` subdirectory with `manual` prefix
- `null` - Set to `null` to explicitly skip that metadata type

**Complete ES-DE Example**:
```json
"metadata_mappings": {
  "Images/Box - Front": "images/box2dfront",
  "Images/Box - Back": "images/box2dback",
  "Images/Box - 3D": "images/box3d",
  "Images/Screenshot - Gameplay": "images/screenshot",
  "Images/Screenshot - Game Title": "images/titlescreen",
  "Images/Fanart - Background": "images/fanart",
  "Images/Banner": "images/marquee",
  "Images/Arcade - Marquee": "images/marquee",
  "Images/Cart - Front": "images/cover",
  "Images/Clear Logo": "images/wheel",
  "Videos": "videos/video",
  "Manuals": "manuals/manual",
  "Music": null
}
```

**How Files Are Named**:
- When `rename_metadata_to_match_rom: true` (ES-DE): `mario.png`, `mario.mp4`, `mario.pdf`
- When `rename_metadata_to_match_rom: false`: `mario-box2dfront.png`, `mario-video.mp4`, `mario-manual.pdf`

**Final Path Examples** (with ES-DE settings):
- ROM: `~/.emulationstation/ROMs/nes/Super Mario Bros.nes`
- Metadata: `~/ES-DE/downloaded_media/nes/images/Super Mario Bros.png`
- Metadata: `~/ES-DE/downloaded_media/nes/videos/Super Mario Bros.mp4`

**Important Notes**:
- ES-DE only allows **one file per metadata type per game**
- If multiple files exist (e.g., 4 screenshots), the script will prompt you to choose one
- Use the `a` option during selection to auto-select the first file for remaining prompts
- Unmapped metadata directories will be skipped and reported
- Set metadata to `null` to explicitly ignore it (e.g., `"Music": null`)
- **Metadata is checked for all games**, including those that already exist in the destination
  - This ensures any new or missing metadata is exported even if the game ROM was previously exported
  - Existing metadata files are not overwritten unless `--force` is used

### Metadata Filename Matching

The `rename_metadata_to_match_rom` configuration option controls how metadata files are named:

**When `true` (ES-DE requirement)**:
- Metadata files are renamed to match the ROM filename exactly (excluding extension)
- Example: ROM file `Super Mario Bros.nes` → metadata files:
  - `Super Mario Bros.png` (box art image)
  - `Super Mario Bros.mp4` (video)
  - `Super Mario Bros.pdf` (manual)
- **Required for ES-DE**: ES-DE matches metadata to ROMs by filename
- Subdirectory structure is maintained (e.g., `images/`, `videos/`, `manuals/`)

**When `false` (legacy format)**:
- Metadata files use game name with metadata type prefix
- Example: ROM file `Super Mario Bros.nes` → metadata files:
  - `Super Mario Bros-box2dfront.png` (box art image)
  - `Super Mario Bros-video.mp4` (video)
  - `Super Mario Bros-manual.pdf` (manual)
- Used by formats that support multiple files per metadata type

**Configuration**:
```json
"rename_metadata_to_match_rom": true  // ES-DE style filename matching
```

### Metadata Subdirectory Selection

**Global Pre-Scanning**: Before processing any games, the script performs a comprehensive scan of **all platforms** in your Master Archive to discover all unique subdirectories across all metadata types. This creates a unified list of regional and variant options (e.g., "Europe", "North America", "Japan", "World", "Cocktail", "Upright").

You'll be prompted **once** at the beginning to select which subdirectories to use globally. Your selection will be applied to ALL platforms and ALL metadata types throughout the entire export session.

**Example Workflow**:

```
Scanning all platforms for metadata subdirectories...
  Scanning platform 50/150...
  
======================================================================
GLOBAL METADATA SUBDIRECTORY SELECTION
======================================================================
Found 8 unique subdirectory(ies) across all platforms:
  1. Asia
  2. Europe
  3. Japan
  4. North America
  5. USA
  6. World
  7. Cocktail
  8. Upright

These subdirectories contain regional, language, or variant metadata.
Select which ones to use - your selection will be applied to
ALL platforms and ALL metadata types throughout the export.

Options:
  Enter numbers (comma-separated) to select specific subdirectories
  a - Select all subdirectories
  n - Skip subdirectories (search base directories only)
======================================================================

Select subdirectories [1-8/a/n]: 3,4,5,6
✓ Selected 4 subdirectory(ies): Japan, North America, USA, World

✓ Global subdirectory selection complete. These will be used for all platforms.
```

**Selection Options**:
- **Comma-separated numbers**: Select specific subdirectories (e.g., `3,4,5,6` for Japan, North America, USA, World)
- **a**: Select all subdirectories (include all regional variants)
- **n**: Skip subdirectories entirely (search only base directories)
- **One-time selection**: Your choice applies to the entire export session
- **Intelligent filtering**: Only subdirectories that actually exist in each metadata path are used

**Benefits**:
- ✅ **Single prompt**: Make one decision for the entire export
- ✅ **Comprehensive view**: See all available regional/variant options upfront
- ✅ **Consistent results**: Same subdirectories used across all platforms
- ✅ **Efficient**: No repeated prompting during game processing

After the global subdirectory selection completes, the script proceeds to process each platform. If multiple files are found within the selected subdirectories for a specific game, you'll still be prompted to choose which file to use.

### Metadata File Selection Interactive Mode

When multiple metadata files exist for a game:

```
======================================================================
Multiple Images/Screenshot - Gameplay files found for: Super Mario Bros
Destination allows only one file as: screenshot
======================================================================
  1. Super Mario Bros-01.png (245.3 KB)
  2. Super Mario Bros-02.png (238.7 KB)
  3. Super Mario Bros-03.png (251.2 KB)
  4. Super Mario Bros-04.png (247.9 KB)
  s. Skip this metadata
  a. Always use first file (stop prompting)

Select file [1-4/s/a]: 
```

- Select a number to choose that specific file
- Press `s` to skip this metadata for this game
- Press `a` to automatically use the first file for all remaining conflicts

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
   
   Common file extensions (comma-separated, e.g., .zip,.7z,.bin)
   Extensions [.zip,.7z]: 
   
   Emulator setup:
     Use ES-DE placeholders: %EMULATOR_RETROARCH%, %CORE_RETROARCH%, %ROM%
     Examples:
       RetroArch: %EMULATOR_RETROARCH% -L %CORE_RETROARCH%/[core]_libretro.so %ROM%
       Standalone: /path/to/emulator %ROM%
   
   Emulator type (retroarch/standalone) [retroarch]: retroarch
   RetroArch core name (e.g., mame, nestopia, snes9x): mame
   
   ======================================================================
   CUSTOM SYSTEM XML TO BE ADDED:
   ======================================================================
     <system>
       <name>customplatform</name>
       <fullname>Custom Platform Name</fullname>
       <path>./roms/customplatform</path>
       <extension>.zip,.7z</extension>
       <command>%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mame_libretro.so %ROM%</command>
       <platform>customplatform</platform>
       <theme>customplatform</theme>
     </system>
   ======================================================================
   
   Target file: /home/user/.emulationstation/custom_systems/es_systems.xml
   ```

**ES-DE Command Template Placeholders:**
- `%EMULATOR_RETROARCH%` - Path to RetroArch executable
- `%CORE_RETROARCH%` - Path to RetroArch cores directory
- `%ROM%` - Path to the ROM file being launched
- Use `[corename]_libretro.so` format for RetroArch cores (e.g., `mame_libretro.so`, `snes9x_libretro.so`)

The script will automatically:
- **Check for existing custom systems** from previous runs to avoid duplicates
- Show you exactly what XML will be added to the custom systems file
- Create/update `~/.emulationstation/custom_systems/es_systems.xml`
- Add the platform mapping for the current session
- Create the appropriate directory structure

**Note:** If you run the script multiple times with the same unmapped platform:
- First run: Prompts you to create the custom system
- Subsequent runs: Automatically detects the existing custom system and uses it
- Output: `ℹ Using existing custom system for 'Platform Name': systemname`

### Adding Platform Mappings Manually

Edit `fe_formats.json` and add entries to `platform_mappings`:

```json
"platform_mappings": {
  "Nintendo Entertainment System": "nes",
  "Super Nintendo Entertainment System": "snes",
  "Your Custom Platform": "customplatform"
}
```

### Handling Unmapped Metadata Directories

The script will automatically skip metadata directories that aren't mapped in `metadata_mappings` and notify you:

```
Skipping unmapped metadata directory: Images/3D Box (not supported by es-de)
Skipping unmapped metadata directory: Images/Disc (not supported by es-de)
```

To add support for additional metadata types, edit the `metadata_mappings` in `fe_formats.json`:

```json
"metadata_mappings": {
  "Images/Box - Front": "images/box2dfront",
  "Images/3D Box": "images/box3d",
  "Images/Disc": "images/cover",
  "Videos": "videos/video"
}
```

Remember to use the `"subdirectory/prefix"` format for the destination values.

If a metadata type isn't supported by the destination format, set it to `null`:

```json
"metadata_mappings": {
  "Music": null
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

### Progress Indicators and Verbose Mode

The script provides feedback during long operations to show that it's actively working:

**Normal Mode** (default):
- Shows periodic progress updates every 10 games during metadata processing
- Example: `→ Processing game 50/200...`
- Minimal console output focused on results

**Verbose Mode** (`--verbose` or `-v`):
- Shows detailed task-by-task progress with full paths
- Displays what the script is doing at each step
- Shows file counts during scanning (every 100 items)
- Displays full source and destination paths
- Logs subdirectory discovery and selection
- Shows file matching and selection process
- Indicates success/failure for each operation

**Example verbose output:**
```
[Game 15/50] Super Mario Bros
======================================================================
  Checking: Images/Box - Front
    → Source: /mnt/archive/Metadata/Images/NES/Box - Front
    → Checking for subdirectories...
    → Found 2 subdirectory(ies)
    → Searching for files matching: Super Mario Bros...
    ✓ Found 1 matching file(s)
    → Using: Super Mario Bros.png
    → Building destination path...
    → Destination: ~/ES-DE/downloaded_media/nes/images/Super Mario Bros-box2dfront.png
    → Creating symlink...
    ✓ Success
```

**When to use verbose mode:**
- Troubleshooting issues (missing files, incorrect paths)
- Understanding what the script is doing
- Verifying correct source/destination mapping
- Debugging slow operations

### ES-DE AppImage/Flatpak Metadata Location

If you're using ES-DE as an AppImage or Flatpak, metadata may be stored in a different location than the default. To configure this:

1. Edit `fe_formats.json` and set the `metadata_path` field:
   ```json
   {
     "formats": {
       "es-de": {
         "metadata_path": "~/ES-DE/downloaded_media",
         ...
       }
     }
   }
   ```

2. Common metadata locations:
   - **Default**: Metadata stored in `~/ROMs/[platform]/images/`
   - **AppImage/Flatpak**: Metadata may be in `~/ES-DE/downloaded_media/[platform]/images/`
   
3. The script will log the metadata path being used during initialization

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

1. **Symlink Creation Failed (Windows)**:
   
   **Error**: `Symlink creation failed: Insufficient privileges`
   
   **Solution**: On Windows, creating symlinks requires special privileges. You have two options:
   
   **Option A - Enable Developer Mode (Recommended)**:
   - Open Settings → Update & Security → For Developers
   - Enable "Developer Mode"
   - Restart your terminal/command prompt
   - Run the script again
   
   **Option B - Run as Administrator**:
   - Right-click Command Prompt or PowerShell
   - Select "Run as Administrator"
   - Navigate to your script directory
   - Run the script
   
   **Option C - Use Copy Mode Instead**:
   ```bash
   # Copy files instead of creating symlinks (works without special privileges)
   python init.py --symlink=false --platform "nes" --games ALL
   ```

2. **Symlinks Not Being Created**:
   
   Check the log file `archive_export.log` for detailed error messages. Common issues:
   - Source files don't exist (check Master Archive mount)
   - Destination directory permissions
   - Insufficient privileges (see #1 above)
   
   Enable verbose logging to see what's happening:
   ```bash
   python init.py --verbose --platform "nes" --games ALL
   ```

3. **Permission Denied**: Run with appropriate privileges or check directory ownership
   ```bash
   # Linux/macOS - Check directory permissions
   ls -ld /path/to/directory
   
   # Create directory manually with correct permissions
   mkdir -p /path/to/directory
   chmod 755 /path/to/directory
   
   # Windows - Check folder properties
   # Right-click folder → Properties → Security tab
   ```

4. **Source Not Found**: Ensure the NFS mount is connected
   ```bash
   # Check if mount point exists
   mount | grep Emulators
   
   # Remount if necessary
   sudo mount -t nfs 192.168.1.3:/Emulators /mnt/Emulators
   ```

5. **Invalid Archive Structure**: Verify the master archive layout matches expectations
   ```bash
   # Check structure
   ls -la "/mnt/Emulators/Master Archive/"
   # Should show: Games/ and Metadata/ directories
   ```

6. **Configuration Errors**: Validate your `fe_formats.json` file
   ```bash
   # Test JSON syntax
   python -m json.tool fe_formats.json
   ```
