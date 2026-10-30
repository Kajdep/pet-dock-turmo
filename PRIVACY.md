# Pet Dock privacy policy

Pet Dock is an open-source Windows desktop companion. It captures a window or desktop area selected by the user, optionally adds local computer statistics, and streams the resulting images to a compatible USB display. The pet itself keeps running in its original host application.

## Local capture and hardware readings

Capture frames are processed in memory on the computer and sent to the selected display. Pet Dock does not read chat history, extract pet assets, create a remote pet service, or automatically upload screenshots. A desktop-area capture includes anything visible inside that area; choose it carefully if other windows can overlap.

CPU, memory, disk, network and available GPU/sensor readings are collected locally. An optional GPU provider is queried through a local subprocess. Unavailable sensors are displayed as N/A rather than replaced by guesses. Pet Dock does not install monitoring drivers or inject into other applications.

## Accounts and network use

Pet Dock does not require an app account, login, API key, subscription or cloud service. It does not automatically transmit app telemetry, screenshots or diagnostic reports. The host application that runs the original pet has its own functionality and privacy policy, which Pet Dock does not control.

Links to source, downloads, documentation or retailer pages open external services. Those services receive ordinary browser requests under their own privacy policies. A website visitor’s browser requests and the website host’s operational logs are separate from the Windows app’s local operation.

## Settings, assets and recovery

Settings, logs, imported backgrounds/fonts and experimental desktop-pet recovery state stay on the user's PC. Installed and portable versions use their documented local data folders. Microsoft Store builds use per-package Windows user storage rather than writing into the program installation folder. Store uninstall and data retention follow Windows package-management behavior; do not rely on uninstall to preserve a backup.

Imported background images are copied into the app’s private asset folder and converted without the original image metadata. The original image is not modified. Fonts and images should be used only when the user has the necessary rights. Capture artifacts and imported personal assets are not part of the public source repository.

## Diagnostics are opt-in to share

Users can deliberately create local diagnostic reports. A report may include system versions, window titles, process names, USB/driver identifiers and file paths. Reports are not automatically sent anywhere. Review and redact a report before choosing to post it publicly.

## Control and contact

Stop capture or close Pet Dock to stop mirroring. Restore an experimentally moved off-screen pet before deleting recovery state. Close the app before backing up, migrating or removing its data files.

Privacy questions and bug reports can be submitted through the [project's GitHub issues](https://github.com/Kajdep/pet-dock-turmo/issues). Do not include private chat content, unredacted reports or personal files in a public issue. The application source is available under GPL-3.0-or-later for inspection.
