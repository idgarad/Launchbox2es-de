# Platform Mapping Guide

## Overview

The Master Archive Export Tool uses platform mappings to translate platform names from your Master Archive to the specific directory names required by different emulation frontends like ES-DE.

## Why Platform Mapping?

Different emulation frontends have different requirements for system directory names:

- **Master Archive**: Uses full descriptive names like "Nintendo Entertainment System", "Super Nintendo Entertainment System"
- **ES-DE**: Requires specific short names like "nes", "snes", "genesis"
- **Other frontends**: May have their own naming conventions

## How It Works

### 1. Platform Mappings in `fe_formats.json`

The `platform_mappings` section defines the translation:

```json
{
  "formats": {
    "es-de": {
      "platform_mappings": {
        "Nintendo Entertainment System": "nes",
        "Super Nintendo Entertainment System": "snes",
        "Sega Genesis": "genesis"
      }
    }
  }
}
```

### 2. Automatic Translation

When exporting, the script:
1. Reads platform name from Master Archive (e.g., "Nintendo Entertainment System")
2. Looks up the mapping in `fe_formats.json`
3. Creates directory with the mapped name (e.g., `~/.emulationstation/ROMs/nes/`)

### 3. Handling Unmapped Platforms

#### In Dry-Run Mode:
```bash
python init.py --dry-run --platform ALL --games ALL
```

- Unmapped platforms are skipped
- Listed at the end in "UNMAPPED PLATFORMS" section
- No prompts, just informational

#### In Normal Mode (ES-DE):
```bash
python init.py --platform "Custom Platform" --games ALL
```

If platform isn't mapped, you'll be prompted:

```
======================================================================
UNMAPPED PLATFORM: Custom Platform Name
======================================================================
This platform is not mapped in the es-de configuration.
You can add it as a custom system to ES-DE.

Add as custom system? (y/n): y

Enter system information (press Enter for default):
System name [customplatformname]: myplatform
Full name [Custom Platform Name]: 
Extensions [.zip,.7z]: .bin,.iso
Emulator command [retroarch]: 
RetroArch core (optional): genesis_plus_gx

✓ Successfully added 'Custom Platform Name' as custom system
  System directory: ./roms/myplatform
  You may need to restart ES-DE to see the new system
```

The script will:
- Create/update `~/.emulationstation/custom_systems/es_systems.xml`
- Add the system definition with proper XML structure
- Add the mapping to the current session (no need to edit JSON immediately)
- Create the appropriate directory structure

## Viewing Platform Mappings

### List All Mappings for a Format

```bash
python init.py --show-mappings es-de
```

Output:
```
Platform mappings for ES-DE (EmulationStation Desktop Edition):
======================================================================
Master Archive Platform                            | Destination
----------------------------------------------------------------------
Nintendo Entertainment System                      | nes
Super Nintendo Entertainment System                | snes
Sega Genesis                                       | genesis
...
======================================================================
Total mappings: 95
```

## Adding Platform Mappings

### Method 1: Edit `fe_formats.json` (Recommended)

Add entries to the `platform_mappings` section:

```json
{
  "formats": {
    "es-de": {
      "platform_mappings": {
        "Existing Platform": "existing",
        "Your New Platform": "newplatform",
        "Another Platform": "another"
      }
    }
  }
}
```

**Advantages:**
- Permanent solution
- Available for all future exports
- Can be version controlled

### Method 2: Interactive Addition (ES-DE Only)

Run without `--dry-run` and answer prompts for unmapped platforms.

**Advantages:**
- Quick for one-off platforms
- Automatically creates ES-DE custom system XML
- No manual XML editing required

**Disadvantages:**
- Only adds to ES-DE's custom systems, not to fe_formats.json
- Need to manually add to JSON for future exports

## ES-DE Custom Systems

When you add a custom system interactively, the script creates/updates:

**File**: `~/.emulationstation/custom_systems/es_systems.xml`

**Format**:
```xml
<?xml version="1.0"?>
<systemList>
  <system>
    <name>myplatform</name>
    <fullname>My Platform Name</fullname>
    <path>./roms/myplatform</path>
    <extension>.zip,.7z,.bin</extension>
    <command>retroarch -L genesis_plus_gx %ROM%</command>
    <platform>myplatform</platform>
    <theme>myplatform</theme>
  </system>
</systemList>
```

## Best Practices

### 1. Check Mappings Before Export

```bash
# See what platforms are mapped
python init.py --show-mappings es-de

# Do a dry run to identify unmapped platforms
python init.py --dry-run --platform ALL --games ALL
```

### 2. Add Common Platforms to JSON

If you frequently export certain platforms, add them to `fe_formats.json` rather than using interactive mode each time.

### 3. Use Consistent Naming

When adding custom systems, use ES-DE naming conventions:
- All lowercase
- No spaces (use underscores or hyphens if needed)
- Short and descriptive

### 4. Document Your Custom Systems

Keep notes on custom platforms you've added, including:
- System name
- Emulator and core used
- Supported file extensions
- Any special configuration

## Troubleshooting

### Problem: Platform Shows as Unmapped

**Solution**: Check spelling in `fe_formats.json`. Platform names must match exactly.

```bash
# Compare Master Archive platforms
ls "/mnt/Emulators/Master Archive/Games"

# Compare with mappings
python init.py --show-mappings es-de | grep -i "platform name"
```

### Problem: Custom System Not Appearing in ES-DE

**Solutions**:
1. Restart ES-DE
2. Check file location: `~/.emulationstation/custom_systems/es_systems.xml`
3. Verify XML syntax is valid
4. Check ES-DE logs for errors

### Problem: Want to Remove Custom System

**Solution**: Edit `~/.emulationstation/custom_systems/es_systems.xml` and remove the `<system>` block.

## Current ES-DE Mappings

The script includes 95+ pre-configured platform mappings for ES-DE, including:

- **Nintendo**: NES, SNES, N64, GameCube, Wii, Wii U, Switch, GB, GBC, GBA, DS, 3DS, Virtual Boy
- **Sony**: PlayStation 1-4, PSP, PS Vita
- **Sega**: Master System, Genesis, CD, 32X, Saturn, Dreamcast, Game Gear
- **Microsoft**: Xbox, Xbox 360
- **Atari**: 2600, 5200, 7800, Jaguar, Lynx, ST
- **Arcade**: MAME, CPS1/2/3, Neo Geo, Naomi, Model 2/3, Cave, Atomiswave
- **Computer**: Commodore 64, Amiga, DOS, Apple II, MSX, ZX Spectrum
- **And many more...**

Use `--show-mappings es-de` to see the complete list.

## Summary

Platform mapping ensures your Master Archive's game library is correctly organized for any emulation frontend, with ES-DE support including automatic custom system creation for unmapped platforms.
