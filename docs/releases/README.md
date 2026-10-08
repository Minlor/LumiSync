# Releases

Use the version tag alone as the GitHub release title, for example `0.8.1`.
Keep release notes focused on user-visible changes and relevant requirements.

GitHub releases contain the three desktop downloads:

- `LumiSync-Windows-x64-onefile.exe`
- `LumiSync-Windows-x64-portable.zip`
- `LumiSync-x86_64.AppImage`

Publish Python wheels and source distributions to PyPI. Do not duplicate them
or add build/debug artifacts to the desktop release. Keep validation records
in the repository. Never replace a published version tag with different source;
ship runtime fixes as a patch release after Linux and Windows checks pass.
