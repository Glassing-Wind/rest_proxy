# Paste into the Mac Shortcut's Run Shell Script action.
# Input: Provided Input from Ask for Input. Pass input: to stdin.
cd /Users/michaelmarler/Projects/rest_proxy || exit 1
exec /Users/michaelmarler/Projects/rest_proxy/.venv/bin/python -m scripts.task_capture
