# Changelog

All notable changes to Sharp Air Egypt are listed here. Versions follow the GitHub releases.

## [Unreleased] – v0.3.0 (planned, pending live testing)

> Nothing below is released yet. It depends on the maintainer's live tests.

- Planned: vertical and horizontal airflow and half-degree steps become standard controls, on by default.
- Planned: Rapid fan speed hidden on units that don't support it. Speed switch removed. Sleep switch probably removed.
- Planned: "Enable experimental controls" becomes **Show untested controls** (Soft and Cocoro Air only).

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

[Unreleased]: https://github.com/Omarhkhater/sharp-air-egypt-ha/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/Omarhkhater/sharp-air-egypt-ha/releases/tag/v0.2.2
[0.2.1]: https://github.com/Omarhkhater/sharp-air-egypt-ha/releases/tag/v0.2.1
[0.2.0]: https://github.com/Omarhkhater/sharp-air-egypt-ha/releases/tag/v0.2.0
