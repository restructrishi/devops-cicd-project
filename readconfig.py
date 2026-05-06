import configparser
import sys
from typing import List, Tuple, Optional

def get_config_values(section_key_pairs: List[Tuple[str, str]]) -> List[Optional[str]]:
    """
    Get multiple configuration values at once.
    
    Args:
        section_key_pairs: List of tuples containing (section, key) pairs
        
    Returns:
        List of values corresponding to each (section, key) pair.
        Returns None for any pair that doesn't exist.
    """
    config = configparser.ConfigParser(
        inline_comment_prefixes=(';', '#'),
        comment_prefixes=('#',)
    )
    results = []
    
    try:
        config.read('config.ini')
        
        for section, key in section_key_pairs:
            if section in config and key in config[section]:
                # Get value and manually strip inline comments
                value = config[section][key].strip()
                # Remove inline comments manually if they exist
                if ';' in value:
                    value = value.split(';')[0].strip()
                results.append(value)
            else:
                results.append(None)
                
        return results
        
    except FileNotFoundError:
        print("Error: config.ini file not found", file=sys.stderr)
        return [None] * len(section_key_pairs)

def get_config_value(section: str, key: str) -> Optional[str]:
    """
    Get a single configuration value.
    
    Args:
        section: The config section name
        key: The config key name
        
    Returns:
        The configuration value, or None if not found
    """
    config = configparser.ConfigParser(
        inline_comment_prefixes=(';', '#'),
        comment_prefixes=('#',)
    )
    
    try:
        config.read('config.ini')
        if section in config and key in config[section]:
            # Get value and manually strip inline comments
            value = config[section][key].strip()
            # Remove inline comments manually if they exist
            if ';' in value:
                value = value.split(';')[0].strip()
            return value
        else:
            return None
    except FileNotFoundError:
        print("Error: config.ini file not found", file=sys.stderr)
        return None

if __name__ == "__main__":
    # Check for --multi FIRST
    if len(sys.argv) > 2 and sys.argv[1] == "--multi":
        pairs = []
        for arg in sys.argv[2:]:
            try:
                section, key = arg.split('.')
                pairs.append((section.strip(), key.strip()))
            except ValueError:
                print(f"Error: Invalid format '{arg}'. Use 'section.key'", file=sys.stderr)
                sys.exit(1)
        
        values = get_config_values(pairs)
        
        for (section, key), value in zip(pairs, values):
            if value is not None:
                print(f"{section}.{key} = {value}")
            else:
                print(f"{section}.{key} = NOT FOUND", file=sys.stderr)
        
        if None in values:
            sys.exit(1)
        else:
            sys.exit(0)
    
    elif len(sys.argv) == 3:
        section_name = sys.argv[1]
        key_name = sys.argv[2]
        value = get_config_value(section_name, key_name)
        
        if value is not None:
            print(value)
            sys.exit(0)
        else:
            print(f"Error: Section '{section_name}' or key '{key_name}' not found", file=sys.stderr)
            sys.exit(1)
    
    else:
        print("Usage:", file=sys.stderr)
        print("  Single value: python script.py <section> <key>", file=sys.stderr)
        print("  Multiple values: python script.py --multi 'section1.key1' 'section2.key2' ...", file=sys.stderr)
        sys.exit(1)
