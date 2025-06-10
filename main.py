#!/usr/bin/env python3
"""
Heroic to Lutris Game Importer
Adds installed Heroic games to the Lutris database using the modern
YAML configuration file method, ensuring executable and Wine prefix are set correctly.
"""

import json
import sqlite3
import os
import sys
import time
from pathlib import Path

# Check for yaml dependency
try:
    import yaml
except ImportError:
    print("Error: PyYAML library not found. Please install it with 'pip install PyYAML'")
    sys.exit(1)


def create_slug(title):
    """Create a URL-friendly slug from a game title."""
    return title.lower().replace(' ', '-').replace(':', '').replace("'", "").replace('®', '').replace('™', '').replace(
        '.', '').replace('&', 'and')


def find_lutris_config_dir():
    """Find the directory where Lutris stores game .yml configurations."""
    home = Path.home()
    possible_paths = [
        home / ".local/share/lutris/games",
        home / ".config/lutris/games",
        home / ".var/app/net.lutris.Lutris/config/lutris/games",  # Flatpak
    ]
    for path in possible_paths:
        if path.exists() and path.is_dir():
            return path
    # If not found, try to create the most common one
    try:
        path_to_create = home / ".local/share/lutris/games"
        path_to_create.mkdir(parents=True, exist_ok=True)
        print(f"Created Lutris games config directory at: {path_to_create}")
        return path_to_create
    except OSError as e:
        print(f"Error: Could not find or create a Lutris games config directory: {e}")
        return None


def get_heroic_game_config(app_name, heroic_config_dir):
    """Get Wine configuration for a game from Heroic config files."""
    config_file = heroic_config_dir / f"{app_name}.json"
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                game_config = config_data.get(app_name, {})
                return {
                    'wine_prefix': game_config.get('winePrefix'),
                    'wine_version': game_config.get('wineVersion', {}).get('bin'),
                    'dxvk': game_config.get('autoInstallDxvk', False),
                    'esync': game_config.get('enableEsync', False),
                    'fsync': game_config.get('enableFsync', False)
                }
        except Exception as e:
            print(f"Warning: Error reading config for {app_name}: {e}")
    return {}


def add_heroic_games_to_lutris():
    # Paths
    heroic_library_path = Path.home() / ".config/heroic/sideload_apps/library.json"
    heroic_config_dir = Path.home() / ".config/heroic/GamesConfig"
    lutris_db_path = Path.home() / ".local/share/lutris/pga.db"
    lutris_config_dir = find_lutris_config_dir()

    if not all([heroic_library_path.exists(), lutris_db_path.exists(), lutris_config_dir]):
        print("Error: A required file or directory was not found. Aborting.")
        if not heroic_library_path.exists(): print(f" - Missing: {heroic_library_path}")
        if not lutris_db_path.exists(): print(f" - Missing: {lutris_db_path}")
        if not lutris_config_dir: print(" - Could not find or create Lutris config directory.")
        return

    try:
        with open(heroic_library_path, 'r', encoding='utf-8') as f:
            heroic_data = json.load(f)
    except Exception as e:
        print(f"Error: Could not read Heroic library file: {e}")
        return

    conn = None
    try:
        conn = sqlite3.connect(lutris_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT slug FROM games")
        existing_slugs = {row[0] for row in cursor.fetchall()}
        added_games = 0
        skipped_games = 0

        for game in heroic_data.get('games', []):
            if not game.get('is_installed', False):
                continue

            game_title = game.get('title')
            if not game_title:
                skipped_games += 1
                continue

            game_slug = create_slug(game_title)

            # --- THIS IS THE CORRECTED, ROBUST SECTION ---
            install_info = game.get('install', {})
            executable_path = install_info.get('executable')
            install_dir = install_info.get('path')  # Safely get the path

            if not executable_path:
                print(f"Skipping '{game_title}' - executable path not found in library file.")
                skipped_games += 1
                continue

            # If the specific install path is missing, fall back to the executable's directory
            if not install_dir:
                install_dir = os.path.dirname(executable_path)
            # --- END OF CORRECTED SECTION ---

            app_name = game.get('app_name')

            if game_slug in existing_slugs:
                print(f"Skipping '{game_title}' - slug '{game_slug}' already exists in Lutris.")
                skipped_games += 1
                continue

            heroic_config = get_heroic_game_config(app_name, heroic_config_dir)
            wine_prefix = heroic_config.get('wine_prefix')

            lutris_game_config = {
                'game': {'exe': executable_path, 'prefix': wine_prefix},
                'wine': {
                    'dxvk': heroic_config.get('dxvk', False),
                    'esync': heroic_config.get('esync', False),
                    'fsync': heroic_config.get('fsync', False),
                }
            }

            config_slug = f"{game_slug}-{int(time.time())}"
            yaml_path = lutris_config_dir / f"{config_slug}.yml"

            try:
                with open(yaml_path, 'w', encoding='utf-8') as f:
                    yaml.dump(lutris_game_config, f, sort_keys=False)
            except Exception as e:
                print(f"Error writing YAML file for {game_title}: {e}")
                skipped_games += 1
                continue

            try:
                cursor.execute(
                    "INSERT INTO games (name, slug, runner, executable, directory, installed, configpath) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (game_title, game_slug, 'wine', executable_path, install_dir, 1, config_slug)
                )
                print(f"✓ Added: {game_title}")
                print(f"  - Executable: {executable_path}")
                print(f"  - Wine Prefix: {wine_prefix or 'Not found'}")
                print(f"  - Wrote config to: {yaml_path}")
                added_games += 1
            except sqlite3.Error as e:
                print(f"Error adding {game_title} to database: {e}")
                skipped_games += 1
                if yaml_path.exists():
                    yaml_path.unlink()

        conn.commit()
        print("\n--- Summary ---")
        print(f"Games added: {added_games}")
        print(f"Games skipped: {skipped_games}")

    except Exception as e:
        import traceback
        print(f"\nAn unexpected error occurred: {e}")
        print("--- Traceback ---")
        traceback.print_exc()

    finally:
        if conn:
            conn.close()


def main():
    print("--- Heroic to Lutris Game Importer (Modern Method) ---")
    print("This will add your installed Heroic games to the Lutris database.")
    print("It creates proper YAML configuration files for each game.")
    print("\nIMPORTANT: Make sure Lutris is completely closed before running this script.\n")

    response = input("Continue with import? (y/N): ").lower()
    if response != 'y':
        print("Cancelled.")
        return

    add_heroic_games_to_lutris()
    print("\nDone! You can now open Lutris to see your imported games.")
    print("The games should have their executable paths and Wine prefixes configured correctly.")


if __name__ == "__main__":
    main()