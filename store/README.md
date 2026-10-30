# Microsoft Store MSIX build

This recipe packages the desktop application as a full-trust x64 MSIX. Microsoft Store publication signs MSIX/AppX after certification; listing the existing EXE installer would **not** have that effect. This recipe does not submit an app, accept Store terms, issue a certificate or change release status.

## Supply the reserved identity locally

Copy `identity.example.json` to an ignored local `store-identity.json`, outside version control, and replace the four values with the exact Partner Center identity and reserved display name. Technical identity strings are case-sensitive and must not be corrected or retyped from the marketing name. Package Family Name, Package SID and Store ID are not needed by the manifest.

The Store-facing package version defaults to `1.0.0.0`. The app remains version `0.3.0`. Store requirements reserve the fourth version component as zero and require a nonzero first component; these are two distinct version systems.

## Prepare and package

First build the clean Windows runtime and portable stage using the existing instructions. Commit the reviewed source so `SOURCE.txt` can refer to the exact corresponding public commit. A Store build copies current application source, bundled runtime, the native launcher, notices and supplied artwork into its own stage. It does not change the portable/NSIS stage.

```sh
python tools/build_msix.py --identity-file store-identity.json --prepare-only
python tools/build_msix.py --identity-file store-identity.json --makemsix /path/to/makemsix
```

`makemsix` must be built from Microsoft's MSIX SDK with packing and schema validation enabled. On Windows, use the Windows SDK tool instead:

```powershell
python tools/build_msix.py --identity-file store-identity.json --makeappx 'C:\Program Files (x86)\Windows Kits\10\bin\<SDK-version>\x64\makeappx.exe'
```

The output is `dist/store/Pet-Dock-Store-1.0.0.0-x64-unsigned.msix`, with a SHA-256 sidecar. `--prepare-only` does **not** produce an MSIX. This builder never creates a fake MSIX by simply renaming a ZIP: it invokes Microsoft's packager, then verifies the real footprint files, exact identity, architecture, CRC and every SHA-256 block-map entry. Store review and Windows App Certification Kit remain separate.

## Windows behavior and testing

The package targets Windows Desktop build 19041 or later, x64, and uses the normal-user full-trust desktop entry point. `runFullTrust` is the only requested capability; explain its use for user-selected window capture, USB/serial image streaming and local metrics during certification. It does not ask for elevation, install drivers, add startup entries or require app-account credentials.

`store-mode.txt` makes the app select package-specific writable state rather than its immutable WindowsApps program directory. The child Python process must inherit Store package identity; test this on Windows. A loose preview with no package identity uses a separate `StorePreview` data folder.

The three base PNG logos reuse the owner-supplied illustration, retaining all visible content with transparent padding. No resource qualifiers/localization strings are used, so the manifest references the base files directly. Additional qualified asset sizes should be paired with a Windows MakePRI-generated resource index rather than added blindly.

Test launch, capture, transparency, crop, backgrounds/fonts, live orientation, metrics, USB ownership/test pattern, crash recovery, update and uninstall in the **packaged** context. Successful portable testing is not proof of packaged compatibility. Run Windows App Certification Kit and review any warnings before submission.

An unsigned Store-upload MSIX is not meant to be double-click installed by the public. Use official Windows development registration/test signing only for controlled local testing, or a private Store flight after upload. Do not disable Windows security protections to install an unsigned upload. GitHub EXE/ZIP downloads remain unsigned; Store signing does not retroactively sign them.

## Source and privacy

`PRIVACY.md` is a public app privacy policy. Actual publisher identity input stays local; only the values required in the generated package are emitted. The build excludes local settings, diagnostics, captured frames, test files and development caches. Dependencies keep their original licenses and provenance.

Official references: [MSIX Store package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements), [desktop manifest construction](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-manual-conversion), [full-trust capabilities](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/app-capability-declarations), and [Microsoft MSIX SDK](https://github.com/microsoft/msix-packaging).
