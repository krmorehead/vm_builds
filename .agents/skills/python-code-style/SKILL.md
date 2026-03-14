---
name: python-code-style
description: Python code conventions. Functions return errors, .env parsing strips quotes, type hints required.
---

# Python Code Style

## Rules

1. Functions NEVER call sys.exit(). Return None, raise exception, or return error code.
2. .env parsing MUST strip surrounding quotes from values.
3. Every function MUST have type hints.
4. Use Path from pathlib for file operations.
5. Logging: % formatting for logs, f-strings for non-logging.

## Patterns

### Function error handling

```python
def find_ansible_playbook(playbook: str) -> str:
    if not found:
        raise FileNotFoundError(f"Playbook not found: {playbook}")
    return path

def get_cached_address() -> str | None:
    path = Path('.state/addresses.json')
    if not path.exists():
        return None
    return json.loads(path.read_text()).get('PRIMARY_HOST')

def probe_host(host: str, port: int, timeout: int = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except (socket.timeout, ConnectionRefusedError):
        return False
```

### .env quote stripping

```python
value = value.strip()
if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
    value = value[1:-1]
env[key.strip()] = value
```

### Path handling

```python
def ensure_file_exists(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    return path

def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
```

### Dictionary merging

```python
def merge_dicts(*dicts: dict) -> dict:
    result = {}
    for d in dicts:
        result.update(d)
    return result

config = merge_dicts(default_config, env_config, cli_config)
```

### Logging

```python
import logging
log = logging.getLogger(__name__)

log.debug("Probing host %s", host)  # Use % formatting
msg = f"Built command for {playbook}"  # OK for non-logging
```

## Previous bugs

- sys.exit() in functions → untestable, breaks composition
- Missing quote stripping → SSH fails with literal "192.168.1.100"
- Missing type hints → MyPy errors
