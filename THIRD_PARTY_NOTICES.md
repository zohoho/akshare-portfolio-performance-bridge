# Third-party notices

AkShare–Portfolio Performance Bridge is licensed under GPL-3.0. Third-party
components bundled with, used by, or queried by the application retain their
own licenses and are not relicensed by this project.

## Bundled runtime and libraries

- Python is distributed under the Python Software Foundation License. The
  bundled runtime includes its `LICENSE.txt`.
- AkShare and its Python dependencies retain the licenses declared in their
  package metadata. License and metadata files are preserved under
  `runtime/Lib/site-packages/*.dist-info/` in the installed application.
- The application launcher is produced with PyInstaller. PyInstaller's
  bootloader exception permits distribution of programs built with it under
  the program's own license.
- The Windows installer is produced with Inno Setup and is subject to the Inno
  Setup license.

## External services and names

The bridge reads public market or fund data from AkShare, Yahoo Finance,
Tencent, Eastmoney and selected fund-company websites. Their data, names and
trademarks remain the property of their respective owners and may be governed
by separate terms.

Portfolio Performance and AkShare are independent projects. This bridge is an
unofficial community tool and is not endorsed by, sponsored by, or affiliated
with either project.
