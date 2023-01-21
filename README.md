
Returns vapoursynth clip from  media paths including vapoursynth and avisynth scripts.

Indexing of files is automatic, if needed. d2vwitch for mpeg2 files and ffmsindex if using ffms2.

## Example:
```
import load
my_loader = load.Sources(d2vwitch_dir='F:\\tools')
dataclasses = my_loader.get_data(['video2.mkv','video2.m2ts']
for data in dataclasses
    clip = data.clip
    if data.load_isError:
        log = data.load_log
        #clip was not loaded, can read the log,  abort
    # work with clip here
```

Vapoursynth clip could be obtained:
1. Using Sources().get_data, that inputs list of sources/filepaths and returns a list of Clip_data dataclasses
2. Using Sources().load_source, that inputs one source/filepath and returns a clip (no logging)

    To change settings use GUI:
    import load
    load.settings()

    That would launch a GUI, where first set your d2vwitch and ffmsindex directories if those executables are not in PATH.
    Then all DEFAULT_SETTINGS could be changed, plugins could be changed, added or removed,
    save it. So arguments of loading Source Plugins are customizable.
    This module always checks those stored settings when loading a filepaths.

3. Calling a particular function that does indexing directly - mpeg2_Source(), ffms2_Source(),
   they still index automatically. Or other functions - vs_Source(), avs_Source(), imwri_Read().
   But all settings need to be passed as keyword arguments because stored settings are not used.
