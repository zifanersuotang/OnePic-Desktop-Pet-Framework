# Third-Party Notices

OnePic Desktop Pet Framework depends on third-party projects. Their licenses remain their own.

## Python runtime and development tools

- Python — Python Software Foundation License
- PySide6 / Qt for Python — LGPLv3/GPLv3 or commercial terms, depending on distribution and use
- Pillow — HPND License
- pytest — MIT License
- PyInstaller — GPLv2-or-later with a special exception for bundled applications
- PyYAML — MIT License
- psd-tools — MIT License
- imageio-ffmpeg — BSD-2-Clause License; bundled FFmpeg binaries have separate licensing obligations
- Requests — Apache License 2.0

Consult the exact installed package versions and their license files before distributing a binary.

## Android experiment

- The Gradle Wrapper JAR is distributed under Apache License 2.0 and carries
  META-INF/LICENSE inside the JAR.
- Android platform APIs are provided by the Android SDK under their own terms.

The Android directory is experimental source and is not included in the default Windows build.

## Optional Live2D research source

`src/onepic_desktop_pet/live2d_*.py` and `tools/live2d_web/src/` are integration/build research source. This repository deliberately excludes:

- Live2D Cubism Core;
- prebuilt `live2d.bundle.js`;
- Live2D models, textures, motions, and customer assets.

Anyone enabling or distributing that route must obtain the applicable Live2D licenses and independently audit all JavaScript dependencies. The default PyInstaller build does not collect the Live2D adapter or Qt WebEngine.

## Demo artwork

Bundled OnePic demo artwork is not covered by the MIT code license. See [ASSETS_LICENSE.md](ASSETS_LICENSE.md).
