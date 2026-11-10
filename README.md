# Speaker Bridge

Use a Sonos speaker as a normal Windows sound output.

A small program runs in the system tray. Choose it in the Windows sound menu,
the same way you would choose headphones, and your PC's audio plays on the
Sonos. Choose your headset or speakers instead and the Sonos is left alone.

It has no dashboard, account or telemetry.

This is an independent hobby project. It is not affiliated with, endorsed by
or supported by Sonos, Inc. "Sonos" is a trademark of Sonos, Inc., used here
only to say which speakers the script works with.

## Install

You need a Windows 10 or 11 PC and a Sonos speaker on the same Wi-Fi or
network.

1. On this page, click the green **Code** button, then **Download ZIP**.
2. Unzip it and put the folder somewhere it can stay, such as `Documents`.
3. Open the folder and double-click **install.bat**.
4. Click **Yes** on the Windows prompts that appear.
5. If the installer says to restart the PC, restart it.

That's it. The installer sets up everything Speaker Bridge needs, and it
starts by itself every time you log in.

### What the installer adds

- **Python**, the free language Speaker Bridge is written in, if the PC
  doesn't have it.
- **VB-CABLE**, a driver made by [VB-Audio](https://vb-audio.com/Cable/)
  that adds one extra sound output to Windows. It is donationware: free to
  use, and you can donate to VB-Audio on their site if you find it useful. Windows can only send sound to
  devices it knows about, and a Sonos isn't one of them. VB-CABLE gives
  Windows a stand-in device to play to, and Speaker Bridge passes whatever
  arrives there on to the Sonos. It is downloaded from VB-Audio's own site.
  If you use it at a company or organisation, VB-Audio asks that a licence
  be bought from them.
- **A firewall rule** so the Sonos can fetch the audio from the PC, limited
  to your home network.
- **A startup shortcut** so Speaker Bridge runs when you log in.

If you move the folder later, double-click `install.bat` again.

To remove Speaker Bridge, double-click **uninstall.bat**.

Everything else Speaker Bridge needs is in the `app` folder. You don't need
to open it.

## Use

- **Play on the Sonos:** click the speaker icon in the taskbar (bottom right),
  open the list of sound outputs, and choose
  **CABLE Input (VB-Audio Virtual Cable)**. To give it a friendlier name such
  as "Sonos", go to Settings > System > Sound, click the device, and rename it.
- **Stop:** choose any other output. The Sonos is released after an hour of
  silence, or straight away if you play something else on it from the Sonos
  app or choose **Quit** in the tray menu.
- **Choose the speakers:** right-click the Speaker Bridge icon in the system
  tray (near the clock; it may be behind the **^** arrow) > **Speakers**.
  Every Sonos speaker found on the network is listed. Click a speaker to tick
  it, and click it again to untick it. You can tick as many as you like, and
  they play together in sync. The first one found is used to begin with, and
  your choice is remembered. Use **Search again** if a speaker is missing.
- **Volume:** right-click the tray icon > **Volume**. It sets every ticked
  speaker to the same level.
- **Quit:** right-click the tray icon > **Quit**.

## Things to know

- Sonos buffers audio for a second or two, and no software can remove that.
  Music is fine; video and games will be out of sync with the picture.
- The first second of sound is cut off when playback starts after the Sonos
  has been released, while it reconnects. Short pauses don't cause this.
- If you start something from the Sonos app while PC audio is still going to
  the cable, Speaker Bridge takes the speaker back within about ten seconds.
  Switch the Windows output away from the cable first.
- Ticking more than one speaker puts them in a Sonos group, the same as
  grouping them in the Sonos app. They stay grouped until you untick one or
  ungroup them in the Sonos app.
- If a ticked speaker is grouped with others in the Sonos app, the whole
  group plays.
- Installing VB-CABLE can make it the default sound output. If your sound
  disappears after installing, choose your usual speakers or headset in the
  taskbar's sound menu.

## Troubleshooting

`speaker_bridge.log` in the `app` folder says what Speaker Bridge is doing.

- **Nothing in the Speakers menu:** the PC and the Sonos must be on the same
  network (not a guest network or separate VLAN).
- **The log says "playing on ..." but there is no sound, or the Sonos never
  connects:** the firewall rule is missing. Run `install.bat` again and
  click **Yes** on the prompt.
- **"CABLE Input" is not in the list of sound outputs, or the log says "no
  output device containing 'VB-Audio Virtual Cable' found":** restart the PC.
  If it is still missing, download VB-CABLE from
  [vb-audio.com/Cable](https://vb-audio.com/Cable/), unzip it, right-click
  `VBCABLE_Setup_x64.exe` > **Run as administrator**, click **Install
  Driver**, and restart.

## Credits

The approach comes from [PC2Sonos](https://github.com/Austinshu/PC2Sonos),
which does much more (keeping PC speakers in sync with the Sonos, per-app
streaming, a dashboard). VB-CABLE is made by
[VB-Audio Software](https://vb-audio.com/Cable/); it is donationware and is
not bundled here, only downloaded from VB-Audio by the installer.
Speaker Bridge also uses [SoCo](https://github.com/SoCo/SoCo) and
[PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch).
