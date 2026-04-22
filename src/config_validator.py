"""Config validation for MasterHttpRelayVPN."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import ipaddress
import re


@dataclass
class ConfigError:
    """Represents a single config validation error."""
    field: str
    message: str


# Placeholder values that should fail validation
PLACEHOLDER_AUTH_KEYS = frozenset({
    "",
    "CHANGE_ME_TO_A_STRONG_SECRET",
    "your-secret-password-here",
})

VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


def _validate_auth_key(config: dict[str, Any], errors: list[ConfigError]) -> None:
    """Validate auth_key field."""
    auth_key = config.get("auth_key", "")
    if not auth_key:
        errors.append(ConfigError("auth_key", "auth_key is required"))
    elif auth_key in PLACEHOLDER_AUTH_KEYS:
        errors.append(ConfigError("auth_key", "auth_key cannot be a placeholder value"))
    elif len(auth_key) < 8:
        errors.append(ConfigError("auth_key", "auth_key must be at least 8 characters"))


def _validate_script_id(config: dict[str, Any], errors: list[ConfigError]) -> None:
    """Validate script_id or script_ids fields."""
    script_id = config.get("script_id")
    script_ids = config.get("script_ids")
    
    if not script_id and not script_ids:
        errors.append(ConfigError("script_id", "script_id or script_ids is required"))
        return
    
    placeholder = "YOUR_APPS_SCRIPT_DEPLOYMENT_ID"
    
    if script_id:
        if not isinstance(script_id, str):
            errors.append(ConfigError("script_id", "script_id must be a string"))
        elif script_id == placeholder:
            errors.append(ConfigError("script_id", "script_id cannot be placeholder"))
    
    if script_ids:
        if not isinstance(script_ids, list):
            errors.append(ConfigError("script_ids", "script_ids must be a list"))
        elif len(script_ids) == 0:
            errors.append(ConfigError("script_ids", "script_ids cannot be empty"))
        elif not all(isinstance(s, str) and s != placeholder for s in script_ids):
            errors.append(ConfigError("script_ids", "all script_ids must be valid strings"))


def _validate_port(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate a port number is in valid range."""
    if value is None:
        return
    if not isinstance(value, int):
        errors.append(ConfigError(field, f"{field} must be an integer"))
    elif not 1 <= value <= 65535:
        errors.append(ConfigError(field, f"{field} must be 1-65535"))


def _validate_ip(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate an IP address."""
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(ConfigError(field, f"{field} must be a string"))
        return
    try:
        ipaddress.ip_address(value)
    except ValueError:
        errors.append(ConfigError(field, f"{field} must be a valid IP address"))


def _validate_host(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate a host (IP or hostname)."""
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(ConfigError(field, f"{field} must be a string"))
        return
    # Allow empty for default
    if not value:
        return
    # Try as IP first
    try:
        ipaddress.ip_address(value)
        return
    except ValueError:
        pass
    # Check as hostname (alphanumeric, dots, hyphens)
    if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$", value):
        errors.append(ConfigError(field, f"{field} must be a valid IP or hostname"))


def _validate_domain(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate a domain name."""
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(ConfigError(field, f"{field} must be a string"))
        return
    if not value or " " in value:
        errors.append(ConfigError(field, f"{field} must be a valid domain"))


def _validate_list_of_strings(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate a list contains only strings."""
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(ConfigError(field, f"{field} must be a list"))
        return
    for i, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(ConfigError(field, f"{field}[{i}] must be a string"))


def _validate_dict_str_str(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate a dict with string keys and values."""
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(ConfigError(field, f"{field} must be a dict"))
        return
    for k, v in value.items():
        if not isinstance(k, str):
            errors.append(ConfigError(field, f"{field} keys must be strings"))
        if not isinstance(v, str):
            errors.append(ConfigError(field, f"{field}[{k}] values must be strings"))
            continue
        try:
            ipaddress.ip_address(v)
        except ValueError:
            errors.append(ConfigError(field, f"{field}[{k}] must be a valid IP"))


def _validate_log_level(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate log_level is valid."""
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(ConfigError(field, f"{field} must be a string"))
        return
    if value not in VALID_LOG_LEVELS:
        errors.append(ConfigError(field, f"{field} must be one of: {', '.join(VALID_LOG_LEVELS)}"))


def _validate_bool(field: str, value: Any, errors: list[ConfigError]) -> None:
    """Validate a boolean field."""
    if value is None:
        return
    if not isinstance(value, bool):
        errors.append(ConfigError(field, f"{field} must be a boolean"))


def validate_config(config: dict[str, Any]) -> tuple[bool, list[ConfigError]]:
    """Validate all config fields.
    
    Args:
        config: The config dictionary to validate.
        
    Returns:
        Tuple of (is_valid, list of config errors).
    """
    errors: list[ConfigError] = []
    
    # Required fields
    _validate_auth_key(config, errors)
    _validate_script_id(config, errors)
    
    # Port fields
    _validate_port("listen_port", config.get("listen_port"), errors)
    _validate_port("socks5_port", config.get("socks5_port"), errors)
    
    # Host fields
    _validate_host("listen_host", config.get("listen_host"), errors)
    _validate_host("socks5_host", config.get("socks5_host"), errors)
    _validate_host("google_ip", config.get("google_ip"), errors)
    
    # Domain fields
    _validate_domain("front_domain", config.get("front_domain"), errors)
    
    # Boolean fields
    _validate_bool("socks5_enabled", config.get("socks5_enabled"), errors)
    _validate_bool("verify_ssl", config.get("verify_ssl"), errors)
    
    # List fields
    _validate_list_of_strings("block_hosts", config.get("block_hosts"), errors)
    _validate_list_of_strings("bypass_hosts", config.get("bypass_hosts"), errors)
    _validate_list_of_strings("direct_google_exclude", config.get("direct_google_exclude"), errors)
    _validate_list_of_strings("direct_google_allow", config.get("direct_google_allow"), errors)
    
    # Dict fields
    _validate_dict_str_str("hosts", config.get("hosts"), errors)
    
    # Log level
    _validate_log_level("log_level", config.get("log_level"), errors)
    
    return len(errors) == 0, errors