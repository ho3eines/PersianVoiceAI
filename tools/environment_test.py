import sys
import platform
import os

print("Python:", sys.version)
print("Executable:", sys.executable)
print("Platform:", platform.platform())
print("Working Directory:", os.getcwd())

assert sys.version_info[:2] == (3, 12)
assert ".venv" in sys.executable.lower()

print("\nENVIRONMENT TEST: PASSED")
