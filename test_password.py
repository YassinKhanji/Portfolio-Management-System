from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="auto")
hash_val = "$pbkdf2-sha256$29000$vjfG.D/H2Ptf631PCSGkNA$Qdovu67MZ42.0wIziCGYknfmQ2YfLyHrDqkNM2bjbEk"

print(f"Hash value: {hash_val}")
print(f"Testing 'Yassin2002': {pwd_context.verify('Yassin2002', hash_val)}")
print(f"Testing 'yassin2002': {pwd_context.verify('yassin2002', hash_val)}")
