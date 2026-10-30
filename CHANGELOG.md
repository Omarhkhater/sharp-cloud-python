# Changelog

All notable changes to Sharp Air Egypt are listed here. Versions follow the GitHub releases.

## [0.4.0] - TBD

### Added
- HomeKit Bridge swing: on is Fully down and off is Up. Any other vertical position, including Up, shows as off. All seven positions stay selectable in Home Assistant.
- HomeKit Bridge fan: the speed slider is Quiet, Very weak, Weak, and Strong, in that order. Auto is the separate auto toggle. Rapid stays available in Home Assistant when the unit lists it. The older speed names still set the same speeds.
- Humidity stays on the climate entity and on the humidity sensor while the air conditioner is off, so HomeKit Bridge can show it.

## [0.3.2] - 2026-10-30

### Changed
- Status is read every 15 seconds by default, instead of every 60. **Configure → Status refresh** can be 10, 15, 30, or 60 seconds. Existing setups use 15 seconds the next time the integration loads. The Sharp cloud does not push a change from the remote or the SHARP app, so this poll is the earliest Home Assistant can show it. The adapter may still upload an infrared change later than that.
- A poll that is forbidden or rate-limited (HTTP 403 or 429), gets a server error, or cannot reach the cloud waits 30 seconds, then 60, 120, and 300. A successful poll goes back to the chosen interval. A chosen interval longer than the current wait is kept. Home Assistant older than 2025.11.2 stretches the poll interval by the same steps. Newer Home Assistant delays the next poll instead.

### Fixed
- Choosing the same value again within 30 seconds sends the command when a newer cloud reading disagrees. A repeat before any newer reading still sends nothing. Choosing the value that reading already shows clears the temporary value and sends nothing. A status read that was already in flight when the change was sent does not count as a newer reading.
- A half-degree temperature chosen while Home Assistant is still showing an earlier whole-degree change follows the last cloud reading. The whole degree is sent first when the unit has not reached it yet, and it is not sent again when that same whole degree was just sent.
- The climate card no longer keeps its own 30 second vane timer on top of the coordinator's. The card still shows a vane while that command is being sent.

## [0.3.1] - 2026-10-24

### Fixed
- Vertical airflow lists the same seven positions as the SHARP app: Auto, Up, Level, Slightly down, Further down, Fully down, and Swing. The position previously called Horizontal is now Level, and its state value changed from `horizontal` to `level`.
- Choosing a vane position shows on the card immediately. If the change fails, the card snaps back. Picking again while a change is still in progress sends the latest choice.
- A command the cloud refuses says whether the unit is busy, names the refusal code, or names the HTTP status. A status refresh follows every failed write. A failed read-back still says the change could not be confirmed.
- HomeKit Bridge: a target temperature is snapped to the unit's step before it is sent, and Super Jet is a diagnostic sensor so it is not exposed as occupancy.

### Known limitations
- For up to 30 seconds after a change from Home Assistant, a change to that same setting made in the SHARP app may not show in Home Assistant. Each setting has its own 30 seconds, so a later change to a different setting does not keep the earlier one hidden. A lagging cloud reading and a real app change look the same.

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

[0.4.0]: https://github.com/Omarhkhater/sharp-cloud-python/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/Omarhkhater/sharp-cloud-python/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Omarhkhater/sharp-cloud-python/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Omarhkhater/sharp-cloud-python/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Omarhkhater/sharp-cloud-python/releases/tag/v0.2.2
[0.2.1]: https://github.com/Omarhkhater/sharp-cloud-python/releases/tag/v0.2.1
[0.2.0]: https://github.com/Omarhkhater/sharp-cloud-python/releases/tag/v0.2.0
