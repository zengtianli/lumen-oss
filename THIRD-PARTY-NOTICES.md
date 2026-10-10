# Third-party notices

Lumen's original engine, native client, HTML assets and bundled standalone adapters are MIT licensed by zengtianli. The process runtime and document/discovery readers are minimal exports of the author's existing shared implementations. No external professional tool, user account configuration or business data is redistributed.

Source builds install dependencies separately using `requirements.txt`: PyInstaller (GPL-2.0-or-later with a bootloader exception permitting bundled applications), lxml (BSD-3-Clause, with upstream libxml2/libxslt notices), python-docx (MIT), pdfplumber (MIT), pypdf (BSD-3-Clause), Pillow (MIT-CMU), python-pptx (MIT). CPython uses the Python Software Foundation License. Their upstream licenses remain applicable; installing dependencies does not relicense them as MIT.

The source snapshot contains no third-party binary runtime. Local frozen builds retain installed package license trees (including native-library notices), the CPython license and exact build-environment inventory in `Contents/Resources/THIRD-PARTY-LICENSES` and `DEPENDENCIES.json`. The inventory may also list build-only dependencies. Signing, notarization and stapling remain separate requirements for public distribution.

Apple system frameworks and developer tools are external requirements and are not included. Official Codex/Claude CLIs and the optional Folio graph tool are installed separately and retain their respective terms.
