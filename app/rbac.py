"""多用户 RBAC：应用层用户与角色（对应 Utopia 的 owner/admin/editor/viewer）。

角色权限（从低到高）：
- viewer —— 只读
- editor —— 可写事实/文档
- admin  —— 可合并实体/管理用户
- owner  —— 完全控制（首个注册用户）

密码用 hashlib.sha256 + 随机盐哈希（教学用；生产用 argon2/bcrypt，原版用 argon2）。
本模块只提供用户/角色/鉴权的数据与判断；把 has_role 接到具体端点即可实现权限控制。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

ROLES = ("viewer", "editor", "admin", "owner")
_LEVEL = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str = "viewer") -> None:
    if role not in ROLES:
        raise ValueError(f"role 必须是 {ROLES} 之一，收到 {role!r}")
    conn.execute(
        "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
        (username, _hash(password), role),
    )
    conn.commit()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if r and _verify(password, r["password_hash"]):
        return dict(r)
    return None


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()


def has_role(user: dict | None, *roles: str) -> bool:
    """判断用户是否具备 roles 中任一角色（按权限等级取最高）。"""
    if not user or user.get("role") not in _LEVEL:
        return False
    needed = min((_LEVEL.get(r, -1) for r in roles), default=-1)
    return _LEVEL[user["role"]] >= needed


def _hash(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${digest}"


def _verify(password: str, stored: str) -> bool:
    salt, digest = stored.split("$", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == digest
