import sys
from pathlib import Path
sys.path.insert(0, ".")
from config import MISSIONS
m = MISSIONS["artemis-ii"]
print("A2 comm_collection:", m.ia_comm_collection)
m2 = MISSIONS["artemis-i"]
print("A1 comm_collection:", m2.ia_comm_collection)
