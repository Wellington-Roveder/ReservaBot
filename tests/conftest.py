import os
import sys

# Ensure the project root is on sys.path so that `services`, `repository`, etc. are importable
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
