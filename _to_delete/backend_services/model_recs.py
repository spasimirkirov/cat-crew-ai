"""Removed — GPU-sized Hugging Face GGUF recommendations no longer exist.

This depended entirely on the Hugging Face Hub GGUF browser, which was
removed along with the rest of the local llama-server/GGUF stack. LM
Studio has its own model catalog and its own recommendations in-app; the
``recommend_models`` tool was removed from ``tools.py`` accordingly.

Nothing in the app imports this module any more. It is kept as an inert
placeholder only because the environment this change was made from could
not delete files on the user's machine (the device shell has been down
since the Sept 8 Windows update). Safe to delete this file by hand.
"""
