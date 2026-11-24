"""Reset: DeltaPatcher as it was just installed.

Clears: every project (as `project remove --all`: a file in the projects folder is deleted, an added one
only leaves the list), the main settings file back to its defaults, and the cached check for a newer
DeltaPatcher. The window clears its own saved state when it resets.

Never touches: the folders projects update or release and their files (each keeps its own record in
<folder>/.deltapatcher, so adding the project or its project file back finds the files, checks them and
downloads only what differs), the signing key, SSH keys.

Keeps: trusted server host keys, the log, prepared releases (stores), and a copy of the settings files as
they were, in a dated folder beside them, so a reset can be undone by hand. A portable copy (deltapatcher.ini
beside the program) is reset the same way; the copy keeps what its maker put in it.
"""

import os
import shutil
import time

from . import config, projects
from .selfupdate import CHECK_FILE

COPY = "settings-before-reset-"


def copy_folder():
    """Where the copy of the settings files goes: a dated folder beside the main settings file."""
    return os.path.join(os.path.dirname(config.path()), COPY + time.strftime("%Y-%m-%d-%H%M%S"))


def plan():
    """What a reset would do, for the person to read before it is done."""
    every = projects.all_projects()
    return {"settings": every[0].path, "copy": copy_folder(),
            "removed": [dict(p.tag(), kind=p.kind) for p in every[1:]]}


def reset():
    """Reset DeltaPatcher. Returns the copy's folder, the main settings file and the projects removed."""
    main = config.ensure()
    copy = base = copy_folder()
    n = 1
    while os.path.exists(copy):
        n += 1
        copy = "%s-%d" % (base, n)
    os.makedirs(copy)
    shutil.copy2(main, os.path.join(copy, os.path.basename(main)))
    for p in projects.all_projects()[1:]:
        if p.kind == "folder" and not p.missing:
            os.makedirs(os.path.join(copy, projects.FOLDER), exist_ok=True)
            shutil.copy2(p.path, os.path.join(copy, projects.FOLDER, os.path.basename(p.path)))
    removed = projects.remove_all()
    config.write_defaults()
    try:
        os.rmdir(projects.folder())                   # only when nothing else is in it
    except OSError:
        pass
    try:
        os.remove(os.path.join(os.path.dirname(main), CHECK_FILE))
    except OSError:
        pass
    return {"settings": main, "copy": copy, "removed": [dict(p.tag(), removed=how) for p, how in removed]}
