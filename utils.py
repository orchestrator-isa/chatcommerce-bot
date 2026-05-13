# utils.py
import secrets
import hashlib

def generate_admin_token(length: int = 32) -> str:
    """Genera un token seguro para endpoints admin"""
    return secrets.token_urlsafe(length)

def hash_token(token: str) -> str:
    """Opcional: hashear token para logs (nunca loguear tokens en claro)"""
    return hashlib.sha256(token.encode()).hexdigest()[:16]

# Uso:
# >>> from utils import generate_admin_token
# >>> print(generate_admin_token())
# 'xK9mP2vL8nQ4rT6wY1zB3cF5hJ7kN0pS'
