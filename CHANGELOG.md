# Changelog

All notable changes to Sharp Air Egypt are listed here. Versions follow the GitHub releases.

## [0.3.1] - 2026-10-24

### Fixed
- Vertical airflow lists the same seven positions as the SHARP app: Auto, Up, Level, Slightly down, Further down, Fully down, and Swing. The position previously called Horizontal is now Level, and its state value changed from `horizontal` to `level`.
- Choosing a vane position shows on the card immediately. If the change fails, the card snaps back. Picking again while a change is still in progress sends the latest choice.
- A command the cloud refuses says whether the unit is busy, names the refusal code, or names the HTTP status. A status refresh follows every failed write.

## [0.3.0] - 2026-10-24

### Added
- Airflow controls (vertical and horizontal vane positions and swing) and half-degree temperature steps are on by default. Verified on the AY-XP12YHE.

### Changed
- **Enable experimental controls** is now **Show untested controls**, and it covers Soft and Cocoro Air only.
- A write is confirmed by reading the air conditioner's status back.

### Removed
- The Sleep and Speed switches.
- Rapid is hidden on units that don't support it.

### Fixed
- A sign-in failure while sending a command asks you to sign in again only when Sharp refuses the saved terminal. Any other sign-in error does not.
- The sign-in failure count is reset when that prompt starts, when sign-in finishes, and when the integration is reloaded, so one failed poll after a reload does not ask you to sign in again.
- A half-degree temperature that crosses a whole degree is sent as the whole degree first, then the half degree. A status byte that is still on the previous degree is not treated as the new temperature.

## [0.2.2] – 2026-10-24

### Fixed
- The Sharp session no longer expires and forces you to sign in again ("Sharp rejected that code"). Stale saved session cookies are dropped before the automatic re-login, and cookies keep their correct path.
- Home Assistant now waits for three failed polls in a row before asking you to sign in again (unless Sharp refuses the terminal outright).
- An outdoor temperature reading that is already more than 6 hours old is no longer restored after a restart.

After updating, restart Home Assistant. You don't need to re-add the integration.

## [0.2.1] – 2026-10-23

### Added
- Room temperature and humidity sensors for each unit.
- The outdoor temperature sensor keeps its last value for up to 6 hours while the AC is off, marked `stale`.
- Integration logo (shown by Home Assistant 2026.3 and newer).

### Changed
- Renamed to **Sharp Air Egypt**. The domain `sharp_cocoro_air` is unchanged, so existing setups keep working.

### Fixed
- Fixed "The air conditioner rejected the command" when the temperature is tapped quickly. Taps are now combined into one command, and commands to the same unit are queued.

## [0.2.0] – 2026-10-23

### Added
- First Home Assistant integration release for Sharp air conditioners on the Sharp Egypt cloud (SHARP Air Control app).
- Browser sign-in (no password stored), climate entity, outdoor temperature, power and energy sensors, Plasmacluster switch, fault and Super Jet (read-only) binary sensors.
- Opt-in experimental controls (airflow, half degrees, Rapid, Sleep/Soft/Speed/Cocoro Air).

[0.3.1]: https://github.com/Omarhkhater/sharp-cloud-python/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Omarhkhater/sharp-cloud-python/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Omarhkhater/sharp-cloud-python/releases/tag/v0.2.2
[0.2.1]: https://github.com/Omarhkhater/sharp-cloud-python/releases/tag/v0.2.1
[0.2.0]: https://github.com/Omarhkhater/sharp-cloud-python/releases/tag/v0.2.0
