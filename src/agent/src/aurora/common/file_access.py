"""统一拒绝文件工具访问常见凭据和私钥路径。"""

from __future__ import annotations

from pathlib import Path

_PRIVATE_DIRECTORIES = frozenset({".git", ".ssh", ".aws", ".azure", ".kube", ".gnupg"})
_PRIVATE_FILES = frozenset(
    {
        ".envrc",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "credentials",
        "credentials.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
_PRIVATE_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".keystore"})
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def is_sensitive_path(path: Path) -> bool:
    """按路径段识别环境配置、凭据目录和私钥，保留普通模板。"""
    for part in path.parts:
        name = part.casefold()
        if name in _PRIVATE_DIRECTORIES or name in _PRIVATE_FILES:
            return True
        if Path(name).suffix in _PRIVATE_SUFFIXES:
            return True
        if (
            name == ".env" or name.startswith(".env.") or name.endswith(".env")
        ) and not name.endswith(_TEMPLATE_SUFFIXES):
            return True
    return False


def ensure_file_access(path: Path) -> Path:
    """检查原始路径及符号链接真实目标，拒绝敏感路径并返回真实路径。"""
    if is_sensitive_path(path):
        raise PermissionError("文件工具拒绝访问敏感文件或凭据目录")
    target = path.resolve()
    if is_sensitive_path(target):
        raise PermissionError("文件工具拒绝访问敏感文件或凭据目录")
    return target
