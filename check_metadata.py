#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/amantux/Projects/mymeal/.worktrees/t_7664e86b/backend')

import app.models  # noqa: F401 - register models
from app.extensions import db
print('Tables in metadata:', [t.name for t in db.metadata.tables.values()])
