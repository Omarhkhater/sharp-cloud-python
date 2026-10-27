<p align="center">
  <img src="custom_components/sharp_cocoro_air/brand/logo.png" alt="Sharp Air Egypt" width="280">
</p>

<h1 align="center">Sharp Air Egypt</h1>

<p align="center">
  <a href="https://hacs.xyz/docs/faq/custom_repositories"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/Omarhkhater/sharp-cloud-python/releases"><img src="https://img.shields.io/github/v/release/Omarhkhater/sharp-cloud-python" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Omarhkhater/sharp-cloud-python" alt="License: MIT"></a>
</p>

Control the Sharp air conditioners you use with the **SHARP Air Control** app (Egypt) right from Home Assistant.

## What it does

Each indoor unit shows up as its own device with:

- **Climate control:** on/off, Cool and Fan only modes (Heat, Dry and Auto are untested), target temperature from 16 to 30 °C, and fan speed (Auto, Quiet, Very weak, Weak, Strong). Rapid is hidden on units that don't support it.
- **Sensors:** room temperature, humidity and outdoor temperature (the outdoor value holds its last reading for up to 6 hours while the AC is off)
- **Power and energy:** live watts and total kWh for the Energy dashboard (readings not yet checked against a meter)
- **Plasmacluster** on/off switch
- **Status:** Super Jet and fault indicators

Airflow controls and half-degree temperature steps are on by default. Vertical positions are Auto, Up, Level, Slightly down, Further down, Fully down, and Swing. Horizontal positions follow the unit; these models offer Straight, Left, Right, and Swing. Soft and Cocoro Air stay behind **Configure → Show untested controls**.

## Install (HACS)

1. In Home Assistant, open **HACS**.
2. Open the ⋮ menu and choose **Custom repositories**.
3. Add `https://github.com/Omarhkhater/sharp-cloud-python` as an **Integration**.
4. Search for **Sharp Air Egypt** and download it.
5. Restart Home Assistant.

Requires Home Assistant 2025.1 or newer. The logo appears in **Settings → Devices & services** on Home Assistant 2026.3 and newer. The HACS list can still say "icon not available".

Manual install: copy `custom_components/sharp_cocoro_air` into your `config/custom_components/` folder and restart.

## Set up

1. Go to **Settings → Devices & services → Add integration → Sharp Air Egypt**.
2. Open the sign-in link shown in the form and sign in with your SHARP Air Control account.
3. The browser ends on a page that won't load (`https://sharp-cocoroair-egypt/?code=…`). That's expected.
4. Copy that full address, paste it into the form, and submit.

Your password never reaches Home Assistant. Each sign-in link works once, so if setup fails, open the link again.

## Supported models

Tested on the **AY-XP12YHE** and **AY-XP18YHE**. Other Sharp ACs in the Egypt SHARP Air Control app may work but are untested. Let us know how yours does!

## Limitations

- **Cloud only.** It works through Sharp's Egypt cloud and needs an internet connection. There's no local control. Home Assistant asks for status every 15 seconds by default. Configure → Status refresh can be 10, 15, 30, or 60 seconds. The cloud does not push a change from the infrared remote or the SHARP app. The adapter uploads that change when it can, and that wait is on Sharp's side. It is not measured here.
- **Super Jet is read-only.** Use the remote to switch it.
- **No Sharp timers.** Use Home Assistant automations or schedules instead.
- **Room readings freeze while the AC is off.** Sharp's cloud caches room temperature and humidity until the unit is turned back on.
- **Unofficial project.** It isn't affiliated with or endorsed by Sharp Corporation or El Araby Group.

## Updates in HACS

HACS does not hear about a release when it is published. A custom repository you downloaded is checked against GitHub about every 48 hours after HACS starts. Opening the repository, or choosing **Update information** from the ⋮ menu on the Sharp Air Egypt row, checks immediately. Reloading HACS starts that 48-hour wait again. Download the update, then restart Home Assistant. You don't need to re-add the integration.

Releases here are tagged `v` plus the version in `manifest.json`, for example `v0.3.2`. That prefix is our convention. HACS skips drafts, and it skips prereleases unless beta updates are turned on.

The icon and logo are in `custom_components/sharp_cocoro_air/brand/`. Home Assistant 2026.3 and newer shows them under **Settings → Devices & services**. The HACS list can still say "icon not available". That list is replaced by the Marketplace integration in Home Assistant 2026.11, which shows the icon from this repository. Nothing further belongs in a brands submission.

## Help and issues

Found a bug or tested a new model? [Open an issue](https://github.com/Omarhkhater/sharp-cloud-python/issues). **Never post** passwords, cookies, tokens, sign-in or redirect URLs, or device IDs.

## License

[MIT](LICENSE) © 2026 Omar Khater
