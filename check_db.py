#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/amantux/Projects/mymeal/.worktrees/t_7664e86b/backend')

import sqlite3
from pathlib import Path

# Find the test database
tmp_path = Path("/private/var/folders/zq/pqzzrvbd3qn7pvdcjx2lfn200000gn/T/pytest-of-amantux")
for db_file in tmp_path.glob("**/test.db"):
    print(f"Checking {db_file}")
    try:
        conn = sqlite3.connect(str(db_file))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        print(f"Tables: {[t[0] for t in tables]}")
        if ("cook_events",) in tables:
            print("cook_events table exists!")
            cols = conn.execute("PRAGMA table_info(cook_events)").fetchall()
            print(f"Columns: {cols}")
        conn.close()
    except Exception as e:
        print(f"Error: {e}")
