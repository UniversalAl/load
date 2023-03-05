'''
Returns vapoursynth clip from  media paths including vapoursynth and avisynth scripts.

Indexing of files is automatic, if needed. d2vwitch for mpeg2 files and ffmsindex if using ffms2.

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

'''
import tempfile
import shutil
import os
import sys
import json
import threading
from typing import Dict, Union, List
import copy
from importlib.machinery import SourceFileLoader
from pathlib import Path
import ctypes
import time

import vapoursynth as vs
from vapoursynth import core
import viewfunc
isAPI4 = viewfunc.isAPI4

try:
    import tkinter as tk
    from tkinter import (ttk, Entry, scrolledtext, Text, Label, Checkbutton, Button, Frame, Toplevel, filedialog, messagebox)
    IS_TKINTER = True
except ImportError:
    IS_TKINTER = False

try:
    from dataclasses import dataclass, field
    isDATACLASS = True
except ImportError:
    isDATACLASS = False

logger, log_string = viewfunc.setup_logger(name=__name__)
logger_error, log_string_error = viewfunc.setup_logger(name=f'{__name__}error')

def get_log():
    return log_string.getvalue()#[:-1]

def get_log_error():
    '''if there is a python error loading vapoursynth script, importlib throws like 10, not much useful error lines, so deleting those'''
    log =  log_string_error.getvalue()[:-1]
    log = [line for line in log.split('\n') if not '<frozen importlib.' in line]
    return '\n'.join(log)

def clear_logs():
    log_string.seek(0)
    log_string.truncate(0)
    log_string_error.seek(0)
    log_string_error.truncate(0)

D2VWITCH_OPTIONS = '--input-range "limited"  --single-input'
D2VSOURCE_KWARGS = dict(rff=True, threads=0)

DEFAULT_PLUGIN_MAP = {
    'mpeg2_Source':           ['m2t mp2 vob mpg mpv m2v',
                               dict(d2vwitch_options=D2VWITCH_OPTIONS)],
    'd2v.Source':             ['d2v',
                               D2VSOURCE_KWARGS],
    'ffms2_Source':           ['avi mkv 264 h264 265 h265 dv webm',
                               {}],
    'imwri_Read':             ['',
                               dict(mismatch=False, alpha=False, float_output=False)],
    'vs_Source':              ['py vpy',
                               {}],
    'avs_Source':             ['avs',
                               {}],
    'avisource.AVISource':    ['',
                               {}],
    'ffms2.Source':           ['',
                               {}],
    'imwri.Read':             ['png jpg jpeg tif tiff exr',
                               dict(mismatch=False, alpha=False, float_output=False)],
    'lsmas.LibavSMASHSource': ['mp4 mov m4v 3gp 3g2 mj2 dvb dcf m21',
                               {}],
    'lsmas.LWLibavSource':    ['m2ts ts mts mxf',
                               {}],
                      }

DEFAULT_SETTINGS = {
    'is_vs_dll_autoload':      True,
    'vs_plugin_dir':           '',
    'ffmsindex_dir' :          '', #if empty string, ffmsindex executable is looked in PATH or current dir
    'd2vwitch_dir' :           '', #if empty string, d2vwitch  executable is looked in PATH or current dir
    'indexing_dir':            tempfile.gettempdir(),
    're_use_indexing':         True,
##    'isAvisynth_installed':    True,
    'plugin_map':              DEFAULT_PLUGIN_MAP
                   }

EXAMPLE_PASSING_SOURCES = '''\n
example of loading five media objects into Preview directly from Python:
import view2
view2.Preview([
       clip,
      'video2.mkv', dict(matrix_in_s ='709'),
      'script.vpy', dict(matrix_in_s ='470bg', vs_output_index=2),
      'video3.mp4',
       clip5, dict(matrix_in_s ='170m')
       ])
'''


INI_FILENAME       = 'load_ini.json'
INI_FILE_DIRECTORY = viewfunc.get_user_app_dir('Load')

SOURCE_DATACLASS_ERROR = ('Wrong sources list.\nDataclasses cannot be mixed with other sources types (paths, vs.Videonodes).\n'\
                          'Dataclasses cannot be passed to load module.\nIf wanting to pass a dataclass for a preview, '\
                          'it must be in a sources argument list alone or with other dataclases only.\n'\
                          'Example: Preview([dataclass1, dataclass2, ...])')

class SourceError(Exception):
    pass

class NoClipError(Exception):
    def __init__(self, e, vs_found_outputs=[]):            
        super().__init__(e)
        self.vs_found_outputs = vs_found_outputs

class ExecutableNotInPATHError(Exception):
    def __init__(self, e, executable):            
        super().__init__(e)
        self.executable = executable

class AvisynthImportError(Exception):
    pass

class ImwriReadError(Exception):
    pass
        
@dataclass
class Clip_data:
    clip:                  vs.VideoNode = None
    load_isError:          bool = False
    load_log:              str  = ''
    load_log_error:        str  = ''
    load_error_exec_path:  str  = ''
    vs_output_index:       int  = None
    vs_found_outputs:      List[int] = field(default_factory=lambda: [])
    source_label:          str  = ''
    source_path:           str  = ''
    source_ext:            str  = ''
    zimg_dict_for_source:  Dict[str, Union[int, float, str, bool]] = field(default_factory=lambda: copy.deepcopy({}))

class Sources:
    '''
    -class is to automatize reading media sources and returns vapoursynth clips or dataclasses with other useful attributes:
       load_log, load_isError, source_label etc.
    -It selects vapoursynth SourcePlugin based on filename extension and that can be customized using GUI.
        - output is a list with dataclasses, if using get_data(),  so each loaded media source file creates one dataclass in that list
        - output is a vapoursynth clip, if using load_source()
        - input sources is a list with media filepaths or even clips (get just passed) AND also optional dictionaries that specify rgb conversion,
          those dictionaries are for view.py module which uses this load module, where rgb conversion dictionary or vapoursynth outputs can be passed
    -Indexing for ffms2 and mpeg files is automatized. Indexing is remembered, or it can be turned off if wanting to index every time
    -imwri reader source plugin is expanded with optional lastnum argument and creates video starting from one source file if firstnum is not used,
                 files for video are selected inteligently, only with the same pattern from a provided source filepath fro that directory.
    -It reads vapoursynth scripts (wanted output can be passed)
    -It reads avisynth scripts, where if avisynth+ is not installed it uses AviSynth.dll and vsavsreader.dll
                 - also remember, if avisynth is not installed, all dll's in that script have to be manually loaded
        
    This Sources class could be avoided calling mpeg_Source, ffms2_Source, vs_Source, avs_Source directly
    if loading manually known file extension, and still using advantage of load.py module and its indexing managment
    '''
    def __init__(self, **kwargs):  #check DEFAULT_SETTINGS to see all possible kwargs
        
        self.settings = load_settings(INI_FILE_DIRECTORY, INI_FILENAME)
        self.settings.update(**kwargs)

        if not isDATACLASS:
            raise ImportError('[load] dataclasses python module needed, which is standard lib from Python 3.7.\n'
                              'If using Python 3.6, you can still install it: pip install dataclasses')

        for key, directory in {'ffmsindex_dir':self.settings['ffmsindex_dir'],
                               'd2vwitch_dir': self.settings['d2vwitch_dir'],
                               'indexing_dir': self.settings['indexing_dir'],
                               'vs_plugin_dir':self.settings['vs_plugin_dir']}.items():
            if not isinstance(directory, str):
                raise ValueError(f'[load] "{key}" must be a string path or empty string')

        if 'fallback_tool_dir' in self.settings:
            '''back up tool dir if executables ffmsindex and d2vwitch,
            if they are not found or not selected by user (mainly used with binaries)'''
            d = {'fallback_tool_dir':self.settings['fallback_tool_dir']}
            self.settings['plugin_map']['ffms2_Source'][1].update(d)
            self.settings['plugin_map']['mpeg2_Source'][1].update(d)
            del self.settings['fallback_tool_dir']
            
    def get_data(self, sources, **kwargs):
        '''     
        example of loading three media files and getting a list of three clip dataclasses:
        import load
        my_loader = load.Sources(d2vwitch_dir=F:\\tools)
        dataclasses = my_loader.get_data(
            ['video1.mpg',
             'video2.mkv', dict(matrix_in_s ='709'),
             'script.vpy', dict(vs_output_index=4),   #if output not specified (like here: 4) it gets first available clip from vs.get_outputs()
             'clip'])                                 #this clip just gets passed as an attribute in new dataclass
        #to get first clip outputs for 'video1.mpg':
        clip         = dataclasses[0].clip
        log          = dataclasses[0].load_log
        load_isError = dataclasses[0].load_isError
        #etc. check Clip_data class for all available attributes
        #to preview loaded clips on screen:
        from view2 import Preview
        Preview(dataclasses)
        
        #or to just preview it and be sure it is not previewed when script is passed to vspipe:
        if __name__ == '__main__':
            from view2 import Preview
            Preview(dataclasses)
        '''        
        clear_logs()                
        try:
            sources, zimg_dicts_for_source, vs_wanted_outputs = self.validate_sources(sources)            
        except (SourceError, Exception) as e:
            data = self.error_data(Clip_data(), e)
            data.source_label = 'source error'
            data.load_log = '[load] could not load source'
            data.load_log_error = get_log_error()
            return [data]

        self.dataclasses = []            
        for i, source in enumerate(sources):  
            data = Clip_data()
            data.zimg_dict_for_source = zimg_dicts_for_source[i]
            data.source_label, data.source_ext = self.get_source_label(source)
            data.source_path = source if not isinstance(source, vs.VideoNode) else ''
            data.vs_output_index = vs_wanted_outputs[i]
            try:
                data.clip = self.load_source(source, vs_wanted_outputs[i], **kwargs)
            except (NoClipError, ExecutableNotInPATHError, AvisynthImportError, ImwriReadError, Exception) as e:
                data = self.error_data(data, e)     
            else:
                if not isinstance(data.clip, vs.VideoNode):
                    logger_error.error(f'Source plugin: {self.label}, did not return vs.VideoNode type:\nclip={data.clip}')
                    data.clip = viewfunc.BLANK
                    data.load_isError = True
                else:
                    try:
                        f = data.clip.get_frame(0)
                    except (AttributeError, vs.Error):
                        '''frame cannot be read, not a concern of this module'''
                        pass
                    else:
                        '''retrieving some clip props data that were stored in global functions'''
                        if 'sources'in f.props:
                            data.source_label = viewfunc.read_prop(f,'sources')

                        if 'vs_output_index' in f.props:
                            data.vs_output_index = viewfunc.read_prop(f, 'vs_output_index')
                            data.clip = viewfunc.delete_prop(data.clip, prop='vs_output_index')

                        if 'vs_found_outputs' in f.props:
                            data.vs_found_outputs = viewfunc.read_prop(f, 'vs_found_outputs')
                            data.clip = viewfunc.delete_prop(data.clip, prop='vs_found_outputs')
            data.load_log       = get_log()
            data.load_log_error = get_log_error()
            clear_logs()
            self.dataclasses.append(data)
        return self.dataclasses


    def load_source(self, source, wanted_output_index=None):
        '''
        -Can be used directly but no logging, no dataclasses, output is just a vapoursynth clip
        -auto loading is still based on a filename extension an indexing managment is used
        -uses stored settings or passed settings (when instantiating)
        
        example of loading a media file:
        import load
        my_loader = load.Sources(d2vwitch_dir=F:\tools)
        clip1 = my_loader.load_source('video1.mpg')
        clip2 = my_loader.load_source('video2.mkv')
        clip3 = my_loader.load_source('script.vpy', wanted_output=2)  #if output not specified, it loads first available from vs.get_outputs() dict
        '''
        if isinstance(source, vs.VideoNode):
            logger.info('source is already vs.VideoNode type of object, vapoursynth clip')
            return source
        source_path = Path(source)
        if not source_path.is_file:
            raise ValueError(f'[load] Not a valid path: {source_path}')
        ext = source_path.suffix[1:].lower()
        if ext == '':
            ext = 'no extension'
        for self.label, (exts, kwargs) in self.settings['plugin_map'].items():
            if ext in exts.split(' '):
                logger.info(f'based on filepath extension, {self.label} will be used')
                labels = self.label.split('.')                
                if self.label == 'mpeg2_Source':
                    return mpeg2_Source(source_path, indexing_dir     = self.settings['indexing_dir'],
                                                     d2vwitch_dir     = self.settings['d2vwitch_dir'],
                                                     re_use_indexing  = self.settings['re_use_indexing'],
                                                     d2vsource_kwargs = self.settings['plugin_map']['d2v.Source'][1],
                                                     **kwargs)
                elif self.label == 'vs_Source':
                    return vs_Source(source_path, wanted_output_index, **kwargs)

                elif self.label == 'avs_Source':
                    return avs_Source(source_path, **kwargs) 
                
                elif self.label == 'ffms2_Source':
                    return ffms2_Source(source_path, indexing_dir    = self.settings['indexing_dir'],
                                                     ffmsindex_dir   = self.settings['ffmsindex_dir'],
                                                     re_use_indexing = self.settings['re_use_indexing'],
                                                     **kwargs)
                elif self.label == 'imwri_Read':
                    return imwri_Read(source_path, **kwargs)
                else:
                    return plugin_Source(labels[0],labels[1], source_path, **kwargs)

                
        else:
            self.label = 'ffms2.Source'
            logger.info('ffms2.Source will be used to load file with unknown extension, possible fail')
            return ffms2_Source(source_path, indexing_dir    = self.settings['indexing_dir'],
                                             ffmsindex_dir   = self.settings['ffmsindex_dir'],
                                             re_use_indexing = self.settings['re_use_indexing'],
                                             **kwargs)

    def get_source_label(self, source):
        if isinstance(source, vs.VideoNode):
            if hasattr(source.format, 'name'):
                return source.format.name, 'none'
            else:
                return 'dynamic_format', 'none'
        path = Path(source)
        stem = path.stem
        ext  = path.suffix
        return f'{stem}{ext}' if len(stem)<=27 else f'~{stem[-27:]}{ext}', ext[1:].lower()

    def error_data(self, data, e):
        data.load_isError = True
        error_type = type(e).__name__
        is_exc_info = False if error_type in ['SourceError', 'NoClipError',
                                              'ImwriReadError', 'ExecutableNotInPATHError', 'AvisynthImportError'] else True
        logger_error.error(msg=e, exc_info=is_exc_info)
        if error_type == 'ExecutableNotInPATHError':
            data.load_error_exec_path = e.executable
        elif error_type == 'NoClipError' and e.vs_found_outputs:
            data.vs_found_outputs = e.vs_found_outputs
        data.clip = viewfunc.BLANK.text.Text(str(e))
        return data

    def validate_sources(self, sources):
        items = sources
   
        if isinstance(items, (vs.VideoNode, str, type(Clip_data))):
            return [items], [{}], [None]
        else:
            if not isinstance(items, (tuple,list)):
                raise SourceError(f'[load] source items must be in a list')
        sources = []
        dicts = []
        for item in items:
            if isinstance(item, (vs.VideoNode, str)):
                if isinstance(item, str) and not Path(item).is_file():
                    raise SourceError(f'[load] Not a valid filepath: {item}')
                sources.append(item)
                dicts.append({})
            elif isinstance(item, dict):
                try:
                    dicts[-1] = item   
                except IndexError:
                    raise SourceError('[load] Dictionary in source arguments is not placed correctly\n' + EXAMPLE_PASSING_SOURCES)
            elif isinstance(item, type(Clip_data())):
                raise SourceError(SOURCE_DATACLASS_ERROR)
            else:
                raise SourceError('[load] Sources must be: vs.VideoNodes or filepaths or dicts\n' + EXAMPLE_PASSING_SOURCES)

        vs_wanted_outputs = []
        for d in dicts:
            if 'vs_output_index' in d:
                vs_wanted_outputs.append(d['vs_output_index'])
                del d['vs_output_index']
            else:
                vs_wanted_outputs.append(None)
            
        return sources, dicts, vs_wanted_outputs

    def save(self):
        clear_logs()
        isDumped = json_dump(INI_FILE_DIRECTORY, INI_FILENAME, self.settings)
        if not isDumped:
            print(get_log_error())
        else:
            print(f'Settings saved to: "{os.path.join(INI_FILE_DIRECTORY, INI_FILENAME)}"')        
        clear_logs()
        
def vs_Source(script_path, wanted_output_index=None, **kwargs):
    '''
    loads vapoursynth vs.VideoNode outputs from vapoursynth script,
    if wanted_output_index (int) is not passed, it gets first available vs.VideoNode if any
    vs.AudioNodes are ignored
    '''
    vs.clear_outputs()
    SourceFileLoader('script', str(script_path)).load_module()
    if isAPI4: type_class = vs.VideoOutputTuple
    else:      type_class = vs.VideoNode
    video_output_indexes = [index for index, output in vs.get_outputs().items() if isinstance(output, type_class)]
    if len(video_output_indexes) == 0:
        raise NoClipError(f'No video output found in "{Path(script_path).name}", vnode.set_output() needs to be added to the script')
    if wanted_output_index is None:
        index = video_output_indexes[0]
    else:
        if wanted_output_index not in video_output_indexes:
            raise NoClipError(f'Wanted video output index: {wanted_output_index} not found in "{Path(script_path).name}", '
                              f'available outputs: {",".join(map(str, video_output_indexes))}',
                               video_output_indexes)
        index = wanted_output_index
    clip = vs.get_output(index)[0] if isAPI4 else vs.get_output(index)
    clip = viewfunc.write_props(clip, vs_found_outputs=video_output_indexes, vs_output_index=index)
    logger.info(f'selected script output index: {index}')
    return clip   

def ffms2_Source(source_path, indexing_dir=tempfile.gettempdir(), ffmsindex_dir='', re_use_indexing=True, **kwargs):
    ffindexing_kwargs = {'indexing_dir':indexing_dir, 'ffmsindex_dir':ffmsindex_dir, 're_use_indexing':re_use_indexing}
    ffindexing_kwargs['fallback_tool_dir'] = kwargs.pop('fallback_tool_dir','')
    logger.info(f'clip=load.ffms2_Source("{source_path}", {kwargs_printed(spacer=" "*30,**ffindexing_kwargs)})')
    ffindex_file = ffmsindex(source_path, **ffindexing_kwargs)
    clip = core.ffms2.Source(source_path, cachefile=ffindex_file, **kwargs)
    if isinstance(clip, (tuple,list)):
        head='clip,_'
        clip = clip[0]
    else:
        head='clip'
    logger.info(f'{head}=core.ffms2.Source("{source_path}",\n{" "*30}cachefile=ffindex_file, {kwargs_printed(spacer=" "*30,**kwargs)})')
    return clip

def imwri_Read(source, **kwargs):
    '''
    beta, weird results could occur,
    the idea is, source is just one image in a python list, that will become first image of new clip where only images of the same name pattern
    will be included in a clip
    
    same as imwri.Read() , but:
    -passing more arguments possible - fpsnum, fpsden, lastnum
    -source cannot be in a printf style form, like "image%06d.png" etc. , use core.imwri.Read() instead if using that
    -source must be a single filename or list of filenames.
    -if source is a single filename:
        -if firstnum is not passed, clip starts with passed source_path, not first image in that directory
        -clip is constructed with images only that have the same numbering pattern as selected source_path has
        -clip is constructed with images only that have the same extension as selected source_path has
    '''
    fpsnum   = kwargs.pop('fpsnum', None)
    fpsden   = kwargs.pop('fpsden', 1)
    firstnum = kwargs.pop('firstnum', None)
    lastnum  = kwargs.pop('lastnum',  None)
    if firstnum is not None: firstnum = int(str(firstnum).strip())
    if lastnum  is not None: lastnum = int(str(lastnum).strip())
    if isinstance(source, (list,tuple)):
        paths = [Path(source_path) for source_path in source]
    else:
        source_path = source
        path = Path(source_path)
        if not path.is_file():
            raise ImwriReadError('[imwri_Read] source must be a filename (filenames will be selected with same numbering pattern and extension) or a python list of filenames only.\n Not in printf style form, like "image%06d.png" etc.')
        paths = list(path.parent.glob(f'*{path.suffix}'))
        if len(paths)>1:
            paths = only_same_pattern(path, paths)
        if len(paths)>1:
            firstnum_loaded = get_num(paths[0].stem)
            if firstnum is not None:
                firstnum_index = max(firstnum-firstnum_loaded, 0)
                if firstnum_index > len(paths):
                    logger.info(f'firstnum={firstnum}, it is higher than numbering of files, it is ignored')
                    firstnum_index = 0
                else: logger.info(f'firstnum={firstnum}')
            else:
                logger.info(f'firstnum was not passed, so it was determined by selected source path: {path.name}, firstnum={get_num(path.stem)}')
                firstnum_index = paths.index(path)
            if lastnum is not None:
                lastnum += 1
                lastnum_index = min(lastnum-firstnum_loaded, len(paths))
                if lastnum_index <= firstnum_index:
                    logger.info(f'lastnum={lastnum-1}, it is less than firstnum={firstnum}, it is ignored')
                    lastnum_index = len(paths)
                else: logger.info(f'lastnum={lastnum-1}')
            else:
                logger.info(f'lastnum was not passed')
                lastnum_index  = len(paths)
            paths = paths[firstnum_index:lastnum_index]
    if   len(paths)==1: paths_print = f'"{paths[0].name}"'
    elif len(paths)<4:  paths_print = ', '.join([f'"{p.name}"' for p in paths])
    else:               paths_print = f'"{paths[0].name}", ...., "{paths[-1].name}"'
    clip = core.imwri.Read(list(map(str, paths)), **kwargs)
    if isinstance(clip, (tuple,list)):
        head='clip,_'
        clip = clip[0]
    else:
        head='clip'    
    logger.info(f'{head}=core.imwri.Read([{paths_print}], {kwargs_printed(spacer=" "*30,**kwargs)})')
    if fpsnum is not None:
        logger.info(f'clip.std.AssumeFPS(fpsnum={fpsnum}, fpsden={fpsden})')
        return clip.std.AssumeFPS(fpsnum=fpsnum, fpsden=fpsden)
    source_label = str(paths[0].name) if len(paths)==1 else f'{paths[0].stem}-{paths[-1].name}'
##    clip = viewfunc.write_props(clip, sources=source_label)
    return clip

def plugin_Source(attr0, attr1, source_path, **kwargs):
    logger.info(f'clip=core.{attr0}.{attr1}("{source_path}", {kwargs_printed(spacer=" "*30,**kwargs)})')
    clip = getattr(getattr(core, attr0), attr1)(source_path, **kwargs)
    return clip

def mpeg2_Source(source_path, indexing_dir=tempfile.gettempdir(), d2vwitch_dir='', re_use_indexing=True,
                 d2vsource_kwargs=D2VSOURCE_KWARGS, **kwargs):
    mpeg2_Source_kwargs = {'indexing_dir':indexing_dir, 'd2vwitch_dir':d2vwitch_dir, 're_use_indexing':re_use_indexing,
                           'd2vsource_kwargs':d2vsource_kwargs }
    if not 'd2vwitch_options' in kwargs:
        logger.info(f'"Mpeg2_Source" needs "d2vwitch_options" in kwargs to index files, using default load.D2VWITCH_OPTIONS instead')
        kwargs.update({'d2vwitch_options':D2VWITCH_OPTIONS})
    if not 'fallback_tool_dir' in kwargs:
        kwargs.update({'fallback_tool_dir':''})
    mpeg2_Source_kwargs.update(kwargs)
    logger.info(f'clip=load.mpeg2_Source("{source_path}", {kwargs_printed(spacer=" "*30,**mpeg2_Source_kwargs)})')
    d2v_file = d2vwitch(source_path, indexing_dir, d2vwitch_dir, re_use_indexing, kwargs['d2vwitch_options'], kwargs['fallback_tool_dir'])
    logger.info(f'clip=core.d2v.Source(d2v_file, {kwargs_printed(spacer=" "*30,**d2vsource_kwargs)})')
    clip = core.d2v.Source(d2v_file, **d2vsource_kwargs)
    logger.info(f'clip=core.std.SetFrameProp(clip, prop="_ColorRange", intval=1) #limited range')
    logger.info(f'if your mpeg2 source is full range set intval value to 0')
    clip = core.std.SetFrameProp(clip, prop="_ColorRange", intval=1)
    return clip

def avs_Source(source_path,  **kwargs):
    '''
    returns vapoursynth clip video from avisynth script using core.avisource.AVISource(path)
    '''
    avisynth_dll_64_path = Path(os.environ.get("SystemRoot")) / "SysWOW64" / "avisynth.dll"
    if not avisynth_dll_64_path.is_file():
        text='''
avisynth.dll not found in SysWOW64 directory, assuming not installed,
it must be in running directory so ctypes can load it (Avisynth+ 64bit version).
Also plugins need to be explicitly loaded in script'''
        logger.info(f'{text}')
        logger.info(f'ctypes.CDLL("./AviSynth.dll")')
        ctypes.CDLL('./AviSynth.dll')
    else:
        logger.info(f'Assuming Avisynth 64bit is installed, path found: "{avisynth_dll_64_path}"')
    logger.info(f'clip=core.avisource.AVISource("{source_path}")')
    try: 
        clip = core.avisource.AVISource(source_path)
    except vs.Error as e:
        raise AvisynthImportError(str(e))
    return clip

def d2vwitch( source, indexing_dir='', d2vwitch_dir='', re_use_indexing=True, d2vwitch_options='', fallback_tool_dir=''):
    index_ext = 'd2v'
    exec_name = 'd2vwitch'
    logger.info(f'indexing, using {exec_name} for: {source}')
    prep = Index_managment_prep(index_ext, exec_name, source, re_use_indexing, d2vwitch_dir, indexing_dir, fallback_tool_dir)
    if not prep.isIndexing:
        #NO INDEXING, using existing index path, just correct input range byte if needed '''
        input_range = 'limited'
        args = d2vwitch_options.split(' ')
        while args:
            arg = args.pop(0)
            if arg =='--input-range' and args:
                input_range = args.pop(0)
        correct_byte_for_range(prep.index_path, input_range)
        return prep.index_path
    #INDEXING 
    cmd_output = f' --output "{prep.index_path}"'  #'--ffmpeg-log-level', 'verbose'
    cmd = f'title d2vwitch creating:  {prep.index_path} | mode con: cols=80 lines=6 | "{prep.exec_path}" {d2vwitch_options} {cmd_output} "{source}"'
    logger.info(f'd2vwitch command line:\n{cmd}')
    #cmd = f'start /wait cmd /c "{prep.exec_path}" {cmd_range} {cmd_single_input} {cmd_output} "{source}" '
    isSuccess = run_process(cmd)
    if isSuccess and Path(prep.index_path).is_file():
        if prep.using_reflist: 
            update_index_path_reference(source, prep.index_path, prep.file_index_ref, prep.indexing_dir, prep.reflist_name, index_ext)
        logger.info(f'created {index_ext} file:\n{index_ext}_file={prep.index_path}')
        return prep.index_path
    else:                     
        logger_error.error(f'ERROR while indexing with {exec_name}')
        return ''        

def ffmsindex(source='', indexing_dir = '', ffmsindex_dir = '', re_use_indexing = True, fallback_tool_dir=''):
    index_ext = 'ffindex'
    exec_name = 'ffmsindex'
    logger.info(f'indexing, using {exec_name} for: {source}')
    prep = Index_managment_prep(index_ext, exec_name, source, re_use_indexing, ffmsindex_dir, indexing_dir, fallback_tool_dir)
    if not prep.isIndexing:
        #NO INDEXING, using existing index path
        return prep.index_path
    #INDEXING
    cmd = f'title ffmsindex creating:  {prep.index_path} | mode con: cols=80 lines=6 | "{prep.exec_path}" -f "{source}" "{prep.index_path}"'
    logger.info(f'ffmsindex command line:\n{cmd}')
    isSuccess = run_process(cmd)
    if isSuccess and Path(prep.index_path).is_file():
        if prep.using_reflist: 
            update_index_path_reference(source, prep.index_path, prep.file_index_ref, prep.indexing_dir, prep.reflist_name, index_ext)
        logger.info(f'created {index_ext} file:\n{index_ext}_file={prep.index_path}')
        return prep.index_path
    else:                     
        logger_error.error(f'ERROR while indexing with {exec_name}')
        return ''
       
def run_process(cmd):
    results = [None]
    p = threading.Thread(target=process, args=(cmd,results))
    p.start()
    p.join()
    if results[0]==0:
        return True
    return False

def process(cmd, result):
    return_code = os.system(cmd)
    result[0]=return_code
    
def correct_byte_for_range(d2v_path, input_range):
    if input_range == 'full': input_range_number = '0'
    else:                     input_range_number = '1'        
    '''find a line with 'YUVRGB_Scale' in d2v file and change byte for YUVRGB_Scale line to '1' or '0' if needed '''
    with open(d2v_path, 'r+') as f:
        offset = 0
        try:
            for line in f:
                if line.strip().startswith('YUVRGB_Scale'):
                    break                       
                offset += len(line)
            if offset:   
                f.seek(offset+13)   #'YUVRGB_Scale=0 or 'YUVRGB_Scale=1'  
                _range = f.read(1)
                if _range != input_range_number:
                    f.seek(0)
                    f.seek(offset)
                    f.write(f'YUVRGB_Scale={input_range_number}')
                    f.close()
                    logger.info(f"correcting input range byte in d2v file to '{input_range_number}' - '{input_range}'")
        except:
            logger_error.error("Not d2v index file or not compatible, input_range byte check failed")


def update_index_path_reference(file, index_path, file_index_ref, indexing_dir, reflist_name, index_ext):
    '''update reflist_name dictionary and save it'''
    if not file_index_ref:
         file_index_ref = json_load(indexing_dir, reflist_name)
    file_index_ref[Path(file).name] = index_path
    old_name = reflist_name
    #and save it again
    reflist_name = '{}list{}'.format(index_ext, random_name())
    isDumped = json_dump(indexing_dir, reflist_name, file_index_ref)
    if isDumped:
        logger.info('reference for new indexed file updated')
        old_file = Path(indexing_dir).joinpath(old_name)
        if old_file.is_file():
            os.remove(old_file)
    else:
        logger_error.error('json failed to store updated reference')

def load_settings(directory, filename):
    settings = load_ini_eval(directory, filename)
    return settings if settings else DEFAULT_SETTINGS

def load_ini_eval(directory, filename):
    if not os.path.isdir(directory):
        logger_error.error(f'directory: "{directory}" for: "{filename}" does not exist, loading default values')
        return {}
    path = os.path.join(directory, filename)
    if not os.path.isfile(path):
        logger.info(f'"{filename}" not found in "{directory}", using default values')
        isDumped = json_dump(directory, filename, DEFAULT_SETTINGS)
        if not isDumped:
            logger_error.error(f'failed to store default {filename} on disk')
        return {}     
    settings = json_load(directory, filename)
    settings = eval_settings(settings)
    return settings
    
def eval_settings(settings):
    settings_eval = {}
    for k, default_value in DEFAULT_SETTINGS.items():
        settings_eval[k] = settings.get(k, default_value)
        if type(settings_eval[k]) != type(default_value):
            settings_eval[k] = default_value
    return settings_eval

    
def json_load(storage, filename):
    path = os.path.join(storage, filename)
    try:
        with open(path, 'r') as f:  
            template = json.load(f)
    except Exception as e:
        logger_error.error(str(e))
        return {}
    else:
        return template

def json_dump(storage, filename, template):
    path = os.path.join(storage, filename)
    try:
        with open(path, 'w') as f:  
           json.dump(template, f)
    except Exception as e:
        logger_error.error(str(e))
        return False
    else:
        return True

def random_name():
    obj = tempfile.NamedTemporaryFile()
    name = os.path.basename(obj.name)
    obj.close()
    return name

def only_same_pattern(path, paths):
    '''to accept only files for imwri.Read with the same pattern like selected file'''
    digits = ''
    path_stem = path.stem
    for ch in reversed(path_stem):
        if ch.isdigit(): digits = f'{ch}{digits}'
        else:            break
    if digits == '': return [path]
    same_non_digit_part = path_stem[:-len(digits)]
    if same_non_digit_part:
        return [p for p in paths if str(p.stem)[:-len(digits)] == same_non_digit_part]
    else:
        stem_length =len(path_stem)
        return [p for p in paths if str(p.stem).isdigit() and len(str(p.stem))==stem_length]
      
def get_num(stem):
    ''' gets a number from trailing digit part of a base filename (called stem in pathlib)'''
    digits = ''
    for ch in reversed(stem):
        if ch.isdigit(): digits = f'{ch}{digits}'
        else:            break
    digits = digits.lstrip('0')
    if digits == '': num = 0
    else:            num = int(digits)
    return num

def kwargs_printed(spacer='', **kwargs):
    '''gets pretty string for printing'''
    if not kwargs: return ''
    new_line = '\n' if spacer else ''
    f = []
    for k,v in kwargs.items():
        if isinstance(v, str): v = f"'{v}'"
        f.append(f'{new_line}{spacer} {k}={v}')
    return ','.join(f)

def settings():
    '''
    Launching UI with settings for load.py module. Those settings can be saved.
    If loading sources using Sources class and get_data() or load_source(), it uses those settings.
    Source also takes arguments to pass settings as well.
    usage:
    import load
    load.settings() #that will launch load settings GUI
    '''
    if not IS_TKINTER:
        print('tkinter module could not be imported, no GUI setup possible, edit DEFAULT_SETTINGS manually in load.py')
        return
    root = tk.Tk()
    settings = load_settings(INI_FILE_DIRECTORY, INI_FILENAME)
    win = Settings_UI(root, settings)
    win.open()
    root.mainloop()

   
class Index_managment_prep:
    '''
    manages indexing automatically
      -re_use_indexing=True will cause to index file only one time, next time loading same source_path, it will use same index
      -to force indexing next time, use: re_use_indexing=False
      -if indexing_dir is not passed, it will store indexes in source_path, so recomending passing temp dir: tempfile.gettempdir()
      -if exec_dir is  not passed, shutil looks for executables (d2vwitch or ffmsindex) in current dir or in PATH
    -some valididy checks
    -logs everything it does
    '''
    
    def __init__(self, index_ext, exec_name, source, re_use_indexing, exec_dir, indexing_dir, fallback_tool_dir=''):

        index_path =  ''       
        file_index_ref = {}
        reflist_name = ''
        using_reflist = False
        if not source:
            raise ValueError(f'[{exec_name}] no source argument for indexing')
                
        if not os.path.isfile(source):
            raise ValueError(f'[{exec_name}] not a file: {source}')
            
        if re_use_indexing not in [True, False]:
            raise ValueError(f'[{exec_name}] re_use_indexing argument must be True or False')
        exec_path = self.get_exec_path(exec_dir, exec_name, fallback_tool_dir)
            
        if indexing_dir:
            #directory for indexing was given , using reference list with generated names for indexed files
            if not os.path.isdir(indexing_dir):
                raise ValueError(f'[{exec_name}] argument indexing_dir is not a directory: "{indexing_dir}"')            
            using_reflist = True
            basename = os.path.basename(os.path.dirname(source))
            indexing_dir = os.path.join(indexing_dir, 'indexing', basename)
            try:
                os.makedirs(indexing_dir)
            except FileExistsError:
                pass
            else:
                logger.info(f'creating directory: {indexing_dir}')
            finally:
                self.isIndexing, self.index_path, self.reflist_name, self.file_index_ref = self.get_index_path_using_reflist(source, index_ext, indexing_dir, re_use_indexing)
        else:
                
            #output directory is source file directory so no reference list with generated names
            indexing_dir = os.path.dirname(source)          
            self.isIndexing, self.index_path = self.get_index_path(source, index_ext, indexing_dir, re_use_indexing)

        self.exec_path     =  exec_path
        self.using_reflist =  using_reflist
        self.indexing_dir  =  indexing_dir
        
    def get_index_path_using_reflist(self, source, index_ext, indexing_dir, re_use_indexing):
        file_index_ref = {}
        #dictionary file_index_ref caries references 'filebase' : 'index path'
        reflist_name = self.get_reflist_name(indexing_dir, index_ext)
        
        index_path = ''
        if re_use_indexing:
            logger.info(f're_use_indexing is enabled, looking for {index_ext} file in temp')
            file_index_ref = json_load(indexing_dir, reflist_name)
            if file_index_ref and isinstance(file_index_ref, dict):
                try:
                    index_path = file_index_ref[os.path.basename(source)]
                except KeyError:
                    index_path = ''
                    logger.info(f"key '{os.path.basename(source)}' index file not found in reference, new {index_ext} file will be created")
                else:
                    if os.path.exists(index_path):
                        logger.info(f'{index_ext}_file="{index_path}"')
                    else:
                        index_path = ''
                        logger.info(f'index file found in reference, but actual {index_ext} file does not exist, will be created')
                        del file_index_ref[os.path.basename(source)]
            else:
                logger.info(f'reference file is not available, new {index_ext} file will be created')
        else:
            logger.info(f're_use_indexing is disabled, new {index_ext} file will be created')
            
        isIndexing = False        
        if not index_path:
            isIndexing = True
            index_path = os.path.join(indexing_dir, random_name() + '.' + index_ext)
                
        return isIndexing, index_path, reflist_name, file_index_ref

    def get_reflist_name(self, indexing_dir, index_ext):
        '''reflist_name is filename that containes jsoned dictionary of  'filebase':'index file'   '''
        reflist_name = ''
        for f in os.listdir(indexing_dir):
            if os.path.isfile(os.path.join(indexing_dir, f)) and f.startswith(index_ext+'list'):
                reflist_name = f
                break
        if not reflist_name:
            reflist_name = '{}list{}'.format(index_ext, random_name())
            isDumped = json_dump(indexing_dir, reflist_name, {'filebase':'index file'})
            if not isDumped:
                logger_error.error(f'json failed to create empty dictionary to store {index_ext} references')
                return ''
            else:
                return reflist_name            
        return reflist_name
    
    def get_index_path(self, file, index_ext, indexing_dir, re_use_indexing):
        ''' non temp output dir - index_path has simple basename from videofile '''
        isIndexing = True
        name = os.path.splitext(os.path.basename(file))[0]
        index_path = os.path.join(indexing_dir, name + '.' + index_ext)
        if re_use_indexing:
            logger.info(f're_use_indexing is enabled, looking for {index_ext} file in "{os.path.dirname(file)}" directory')
            if os.path.isfile(index_path):
                isIndexing = False
                logger.info(f"{index_ext}_file=\"{os.path.basename(file) + '.' + index_ext}\"")
            else:
                logger.info(f'index file not found, new {index_ext} file will be created')
        else:
             logger.info(f're_use_indexing is disabled, new {index_ext} file will be created')
        return isIndexing, index_path

    def get_exec_path(self, exec_dir, exec_name, fallback_tool_dir):
        exec_path=''
        if not exec_dir:
            exec_path = shutil.which(exec_name)
            if exec_path and os.path.isfile(exec_path):
                #logger.info(f'executable {exec_name} found in PATH:\n{exec_path}')
                if not os.access(exec_path, os.X_OK):
                    logger.info(f'executable {exec_name} might not have administrative rights to run')
            else:
                if fallback_tool_dir and os.path.isdir(fallback_tool_dir):
                    logger.info(f'No directory passed for {exec_name}, executable not found in PATH, but fallback_tool_dir passed:\n"{fallback_tool_dir}"')
                    exec_dir = fallback_tool_dir
                else:
                    raise ExecutableNotInPATHError(f'No directory passed for {exec_name} and executable not found in PATH!, '
                                                   f'Use Load module settings window to select {exec_name} directory, then save changes and re-load clip.',
                                                   f'{exec_name}')
        if not os.path.isdir(exec_dir):
            raise ValueError(f'directory for {exec_name} is not a directory: "{exec_dir}"')
        if not exec_path:
            exec_path = os.path.join(exec_dir, exec_name)
        if not os.path.isfile(exec_path):
            exec_path += '.exe'
            if not os.path.isfile(exec_path):
                raise ValueError(f'{exec_name} executable is not in "{exec_dir}"')
        return exec_path
    

class Settings_UI:
    '''
    GUI to edit defaults for load module,
    it saves all defaults to json file, then when load module is used, it reads those defaults,
    to launch GUI use:

    import load
    load.settings()
    '''
     
    labels = [
            (bool, "vapoursynth autoloads DLL's"),
            (str,  'vapoursynth plugin directory  '),
            (str,  'ffmsindex executable directory'),
            (str,  'd2vwitch  executable directory'),
            (str,  'directory to store index files'),
            (bool, 're-use index files'),
            (bool, 'Avisynth+ 64bit is installed')
             ]
    
    def __init__(self, master, settings, parent=None, text_color='#0000AA', font='TkFixedFont', err_bg='#FFE7E7'):
        self.master = master
        self.parent = parent
        self.settings = settings
        self.loaded_tab_general = copy.deepcopy(self.settings)
        self.make_plugin_map_strings(self.loaded_tab_general.pop('plugin_map'))
        self.text_color = text_color
        self.err_bg = err_bg
        self.font = font
        self.disable_saving_settings = False
        self.initialdir = ''
        self.plugin_errors = []
        self.messagebox_open = False
        self.mandatory_plugins = ['mpeg2_Source','d2v.Source','ffms2_Source','vs_Source','avs_Source','imwri_Read']
        self.window = {'on':False, 'tab':0, 'plugin_label':'mpeg2_Source'}
        self.construct_main_window()
        self.construct_tab_general()
        self.construct_tab_plugin_map()
        self.construct_extensions_popup()

    def open(self):
        clear_logs()
        self.tabs.select(self.window['tab'])
        plugin_label = self.window['plugin_label']
        if not plugin_label in self.plugin_box['values']:
            plugin_label = self.plugin_box['values'][0]
        self.fill_gui(plugin_label)
        self.backup_saved_settings()
        self.show()

    def show(self):
        self.window['on'] = True
        self.master.deiconify()
        if self.extensions_popup.window['on']:
            self.show_extensions_popup()
        
    def fill_gui(self, plugin_label):
        self.fill_tab_general(list(self.loaded_tab_general.values()))
        self.fill_plugin(plugin_label)       

    def fill_tab_general(self, values_list):
        for i, k in enumerate(self.loaded_tab_general.keys()):
            getattr(self, f'var_{k}').set(values_list[i])

    def fill_plugin(self, plugin_label):
        self.plugin_box_var.set(plugin_label)
        self.fill_tab_plugin_map(plugin_label=plugin_label) 

    def fill_tab_plugin_map(self, e=None, plugin_label=None):
        if plugin_label is None:
            plugin_label = self.plugin_box_var.get()
        self.window['plugin_label'] = plugin_label
        if plugin_label in self.mandatory_plugins and self.plugin_box.current()<= self.mandatory_plugins.index(plugin_label):
            #hide delete button
            self.delete_plugin_button.lower(self.plugin_frame)
        else:
            self.delete_plugin_button.lift(self.plugin_frame)
        if plugin_label in self.plugin_errors: self.highlight_error_box(self.entry_kwargs)
        else:                                  self.entry_kwargs.config(highlightthickness=0)
        ext, kwargs_string = self.plugin_map_strings[plugin_label]
        self.var_ext.set(ext)
        self.var_kwargs.set(kwargs_string)

    def to_prompt(self, text, tag):
        self.prompt.configure(state='normal')
        self.prompt.insert('end', time.strftime("[%H:%M:%S]")+text+'\n', tag)
        self.prompt.yview('end')
        self.prompt.configure(state='disabled')

    def select_directory(self, i, k):
        directory = filedialog.askdirectory(initialdir=self.initialdir, title=f'Select {self.labels[i][1]}')
        if not directory:
            return
        self.initialdir = directory
        getattr(self, f'var_{k}').set(Path(directory))

    def show_extensions_popup(self):
        self.extensions_popup.window['on'] = True
        self.collect_tab_plugin_map(self.plugin_box_var.get())
        self.extensions_popup.master.deiconify()
  
    def save(self):
        if self.disable_saving_settings:
            return False
        self.disable_saving_settings = True
        self.master.focus()
        self.collect_gui()
        for plugin_label in self.plugin_map_strings:
            if plugin_label in self.plugin_errors:
                self.highlight_error(plugin_label)
                self.tabs.select(1)
                self.disable_saving_settings = False
                return False
        if not self.is_save_needed():
            self.disable_saving_settings = False
            return True
        self.backup_saved_settings()
        self.make_plugin_map_dict()
        self.settings = copy.deepcopy(self.loaded_tab_general)
        self.settings['plugin_map'] = self.plugin_map
        self.dump_settings(self.settings)
        if self.parent is not None and hasattr(self.parent, 'update_load_settings'):
            self.parent.update_load_settings(self.settings)
        self.disable_saving_settings = False
        return True

    def one_messagebox_at_a_time(f):
        def wrap(self):
            if self.messagebox_open == False:
                self.messagebox_open = True
                f(self)
                self.messagebox_open = False
            return
        return wrap
    
    @one_messagebox_at_a_time
    def add_plugin(self):
        plugin_label = self.add_plugin_var.get().strip()
        if plugin_label == '':
            return
        if plugin_label in self.plugin_map_strings:
            messagebox.showinfo(title='Source Plugin cannot duplicate',
                               message=f'As of now, source plugins cannot duplicate')
            return
        labels = list(self.plugin_box['values'])
        labels.append(plugin_label)
        self.plugin_box['values'] = labels
        self.plugin_map_strings.update({plugin_label:['','']})
        self.fill_plugin(plugin_label)
        self.set_extensions_popup()
        self.to_prompt(f'Added {plugin_label} source plugin', 'INFO')
        self.add_plugin_var.set('')
    
    @one_messagebox_at_a_time
    def delete_plugin(self):
        plugin_label = self.plugin_box_var.get()      
        response = messagebox.askquestion (title='Deleting Source Plugin',
                      message=f'Do you want to delete "{plugin_label}" Source Plugin from GUI?',
                      default=messagebox.NO, icon = 'warning')
        if response == 'no':
            return
        self.plugin_box_var.set(self.plugin_box['values'][0])
        self.fill_tab_plugin_map(plugin_label=self.plugin_box['values'][0])
        if plugin_label in self.plugin_errors:
            del self.plugin_errors[plugin_label]
        del self.plugin_map_strings[plugin_label]
        labels = list(self.plugin_box['values'])
        labels.remove(plugin_label)
        self.plugin_box['values'] = labels
        self.set_extensions_popup()
        self.to_prompt(f'Deleted {plugin_label} source plugin', 'INFO')
        
    @one_messagebox_at_a_time
    def reset(self):
        self.master.focus()
        response = messagebox.askquestion (title='Resetting Settings',
                      message=f'All General and Plugin Settings will be lost!\n\n'\
                               'load.DEFAULT_SETTINGS dictionary will be used for new settings.\n\nDo you want to reset?',
                      default=messagebox.NO, icon = 'warning')
        if response == 'no':
            return
        self.loaded_tab_general = copy.deepcopy(DEFAULT_SETTINGS)
        self.make_plugin_map_strings(self.loaded_tab_general.pop('plugin_map'))
        self.fill_gui(self.window['plugin_label'])
        self.set_extensions_popup()
        self.to_prompt(f'load.DEFAULT_SETTINGS loaded into ui', 'INFO')
        
    @one_messagebox_at_a_time
    def close(self):
        self.collect_gui()
        if not self.is_save_needed(): self.closing()
        else:
            response = messagebox.askyesnocancel(title='Save On Close',
                      message='You have unsaved changes.\nDo you want to save before closing?')
            if response == True:
                isSaved = self.save()
                if isSaved: self.closing()
            elif response == False: self.closing()
    
    def closing(self):
        self.window['on'] = False
        if self.parent is None:
            self.master.destroy()
        else:
            #Settings_UI was instantiated by parent module
            self.extensions_popup.master.withdraw()
            self.master.withdraw()

    def tab_clicked(self,event):
        event.widget.focus()
        self.window['tab'] = self.tabs.index(self.tabs.select())
                    
    def highlight_error(self, plugin_label):
        self.highlight_error_box(self.entry_kwargs)
        self.fill_plugin(plugin_label)

    def is_save_needed(self):
        if self.loaded_tab_general_backup == self.loaded_tab_general and self.plugin_map_strings == self.plugin_map_strings_backup:
            return False
        return True
    
    def backup_saved_settings(self):
        self.loaded_tab_general_backup = copy.deepcopy(self.loaded_tab_general)
        self.plugin_map_strings_backup = copy.deepcopy(self.plugin_map_strings)

    def collect_gui(self):
        self.loaded_tab_general = {k : getattr(self, f'var_{k}').get() for k in self.loaded_tab_general.keys()}
        self.eval_collect_tab_plugin_map()

    def eval_collect_tab_plugin_map(self):
        plugin_label  = self.plugin_box_var.get()            
        ext_string, kwargs_string = self.collect_tab_plugin_map(plugin_label)
        isOK = True
        kwargs = {}
        try:
            kwargs = self.string_to_dict(kwargs_string)
        except Exception:
            isOK = False
        else:
            if not isinstance(kwargs, dict):
                isOK = False
        if isOK:
            if plugin_label in self.plugin_errors:
                self.plugin_errors.remove(plugin_label)
        else:
            if plugin_label not in self.plugin_errors:
                self.plugin_errors.append(plugin_label)
            self.to_prompt(f'Syntax error in plugin "{plugin_label}": {kwargs_string}', 'ERROR')

    def collect_tab_plugin_map(self, plugin_label):
        ext_string    = self.var_ext.get().replace(","," ")
        kwargs_string = self.var_kwargs.get()
        self.plugin_map_strings[plugin_label] = [ext_string, kwargs_string]
        self.set_extensions_popup()
        return ext_string, kwargs_string

    def make_plugin_map_dict(self):
        self.plugin_map = {k:[ext, self.string_to_dict(string=kwargs_string)] for k,(ext, kwargs_string) in self.plugin_map_strings.items()}

    def make_plugin_map_strings(self, plugin_map_dict):
        self.plugin_map_strings = {k:[ext, self.dict_to_string(kwargs)] for k,(ext, kwargs) in plugin_map_dict.items()}

    def set_extensions_popup(self):
        self.extensions_popup.text.set(''.join([f'{k: <24}  {ext}\n' for k, (ext,_) in self.plugin_map_strings.items()]))
        
    def _bind_focus(self, widget):
        if self.parent is not None and hasattr(self.parent, 'bind_focus'):
            #instantiated by view module, extra feature, it disables pynput (if on) when typing
            self.parent.bind_focus(widget=widget)            
        else:
            self.bind_focus(widget=widget)
    
    def bind_focus(self, widget):
        widget.bind('<FocusIn>',  self.widget_focused)
        widget.bind('<FocusOut>', self.widget_not_focused)
        
    def highlight_error_box(self, widget):
        widget.config(highlightthickness=2, highlightbackground = "red", highlightcolor= "red")
        
    def widget_focused(self, event):
        event.widget.config(highlightthickness=2, highlightbackground = "green", highlightcolor= "green")      

    def widget_not_focused(self, event):
        event.widget.config(highlightthickness=0)
        
    def dict_to_string(self, kwargs):
        if not kwargs: return ''
        f = []
        for k,v in kwargs.items():
            if isinstance(v, str): v = f'"{v}"'
            f.append(f'{k}={v}')
        return ', '.join(f)

    def string_to_dict(self, string=''):
        string = string.strip()
        if string.endswith(","):
            string = string[:-1]           
        if not string:
            return {}
        try:
            d = dict(pair.strip().split("=") for pair in string.split(","))
            new={}
            for k, v in d.items():
                if v.lower() == 'true':    v = True
                elif v.lower() == 'false': v = False
                else:
                    try:                   v = int(v)
                    except ValueError:
                        try:               v = float(v)
                        except ValueError:
                            if (v[0] == v[-1]) and v.startswith(("'", '"')):
                                v = v[1:-1]         
                new.update({k:v})
        except Exception as e:
##            logger_error.error(str(e)) #debug
##            print(e)
            raise Exception(str(e))
        return new

    def dump_settings(self, settings):
        isDumped = json_dump(INI_FILE_DIRECTORY, INI_FILENAME, settings)
        if not isDumped:
            self.to_prompt(get_log_error(), 'ERROR')
        else:
            self.to_prompt(f'Settings saved to: "{os.path.join(INI_FILE_DIRECTORY, INI_FILENAME)}"', 'INFO')
        clear_logs()
        
    def construct_main_window(self):
        self.master.title('Load module settings')
        self.master.protocol("WM_DELETE_WINDOW", self.close)
        self.master.attributes("-topmost", True)
        self.master.minsize(750, 300)
        self.tabs = ttk.Notebook(self.master)
        self.tabs.bind("<ButtonRelease-1>", lambda event: self.tab_clicked(event))
        self.tab_general = ttk.Frame(self.tabs)
        self.tab_plugin_map = ttk.Frame(self.tabs)
        self.tabs.add(self.tab_general,    text=' General ')
        self.tabs.add(self.tab_plugin_map, text=' Source Plugins ')
        self.tabs.pack(expand=True, fill="both")
        
    def construct_tab_general(self):
        for i, k in enumerate(self.loaded_tab_general):
            frame = self.construct_Frame(self.tab_general)
            setattr(frame, 'label_'+k,  Label(frame, justify='left', anchor='w', font=self.font, text=self.labels[i][1]) )
            getattr(frame, 'label_'+k).pack(side='left')
            if self.labels[i][0] == str:
                self.construct_Entry(frame, k)
                setattr(frame, 'select_'+k, Button(frame, text=' Select ', relief='raised', command= lambda i=i, k=k: self.select_directory(i,k)))
                b=getattr(frame, 'select_'+k)
                b.pack(side='right')
            elif self.labels[i][0] == bool:
                setattr(self, 'var_'+k, tk.BooleanVar())
                var = getattr(self,  'var_'+k)
                setattr(frame, 'ch_'+k, Checkbutton(frame, anchor="e", variable=var, onvalue=True, offvalue=False,
                                                   command=lambda: self.master.focus()))
                getattr(frame, 'ch_'+k).pack(side='left')
        self.prompt = scrolledtext.ScrolledText(self.master, height=1, font=self.font, wrap='word', foreground=self.text_color)
        self.prompt.pack(expand=True, fill='both')
        self.prompt.tag_config('INFO',    foreground=self.text_color,background='white',     selectbackground='light blue')
        self.prompt.tag_config('ERROR',   foreground='red',          background=self.err_bg, selectbackground='light blue')
        self.prompt.tag_config('WARNING', foreground='DarkOrange3',  background='#FFF4DF',   selectbackground='light blue')
        self.prompt.configure(state='disabled')

        #construct buttons at the bottom
        frame = Frame(self.master, height = 26)
        frame.pack(expand=True, fill="both")
        frame.pack_propagate(0)          
        self.save_button = Button(frame, text='   Save All Changes  ', anchor='e', relief='raised', command=self.save)
        self.save_button.pack(side='right')
        self.reset_button = Button(frame, text='  Reset To Default Settings  ', anchor='w', relief='raised', command=self.reset)
        self.reset_button.pack(side='left')
  
    def construct_tab_plugin_map(self):
        #line1
        self.plugin_frame = self.construct_Frame(self.tab_plugin_map, pady=10)
        self.construct_Label(self.plugin_frame, 'Select a Source Plugin: ')
        self.plugin_box_var = tk.StringVar()
        self.plugin_box = ttk.Combobox(self.plugin_frame, width=30, state='readonly', postcommand=self.eval_collect_tab_plugin_map,
                                       textvariable=self.plugin_box_var)
        self.plugin_box.pack(side='left')
        self.plugin_box.bind('<<ComboboxSelected>>', lambda e: self.fill_tab_plugin_map(e))
        self.plugin_box['values'] = list(self.plugin_map_strings.keys())
        self.delete_plugin_button = Button(self.tab_plugin_map, relief='raised', text=' Delete This Plugin ',command = self.delete_plugin)
        self.delete_plugin_button.pack(in_=self.plugin_frame, side='right')
        #line2
        frame = self.construct_Frame(self.tab_plugin_map)
        self.construct_Label(frame, 'extensions:     ')
        self.construct_Entry(frame, 'ext')
        b = Button(frame, text=' view all ',  relief='raised', command=self.show_extensions_popup)
        b.pack(side='right')
        #line3
        frame = self.construct_Frame(self.tab_plugin_map)
        self.construct_Label(frame, 'plugin kwargs:  ')
        self.construct_Entry(frame, 'kwargs')
        #line4
        frame = self.construct_Frame(self.tab_plugin_map, pady=30)
        self.construct_Label(frame, 'Add Plugin:     ')
        self.add_plugin_var = tk.StringVar()
        e = Entry(frame, width=28, font=self.font, foreground=self.text_color, textvariable=self.add_plugin_var)
        e.pack(side='left', fill='both')
        self._bind_focus(e)
        b = Button(frame, relief='raised', text=' Add ',command = self.add_plugin)
        b.pack(side='left')
        
    def construct_Entry(self, master, name):
        setattr(self, f'var_{name}', tk.StringVar())
        setattr(self, f'entry_{name}', Entry(master, font=self.font, foreground=self.text_color, textvariable=getattr(self, f'var_{name}')))
        widget=getattr(self, f'entry_{name}')
        widget.pack(side='left', expand=True, fill='both')
        self._bind_focus(widget)
        
    def construct_Frame(self, master, pady=0):
        frame = Frame(master, height = 26)
        frame.pack(anchor='n', fill='both', expand=False, pady=pady)
        frame.pack_propagate(0)
        return frame

    def construct_Label(self, master, name):
        label = Label(master, justify='left', anchor='n', font=self.font, text=name)
        label.pack(side='left')
    
    def construct_extensions_popup(self):
        win = Toplevel(self.master)
        win.withdraw()
        self.extensions_popup = Extensions(win, self.font)
       

class Extensions:
    def __init__(self, master, font):
        self.master = master
        self.font   = font
        self.window = {'on':False}
        self.construct_window()
        
    def construct_window(self):
        self.master.title('Source Plugin Assignment to Extensions')
        self.master.protocol("WM_DELETE_WINDOW", self.close)
        self.master.attributes("-topmost", True)
        self.master.minsize(300, 200)
        frame = Frame(self.master)
        frame.pack()
        self.text = tk.StringVar()
        label = Label(frame, justify='left', anchor='w', font=self.font, textvariable=self.text)
        label.pack(side='top', expand=True)
        button = Button(frame, text='Copy All', anchor='w', relief='raised', command=self.save_to_clipboard)
        button.pack(side='bottom')
        
    def save_to_clipboard(self):      
        self.master.clipboard_clear()
        self.master.clipboard_append(self.text.get())
        self.master.update()

    def close(self):
        self.window['on'] = False
        self.master.withdraw()

if __name__ == '__main__':
    
    if not IS_TKINTER:
        print('tkinter module could not be imported, no GUI setup possible.')
    else:
        root = tk.Tk()
        settings = load_settings(INI_FILE_DIRECTORY, INI_FILENAME)
        win = Settings_UI(root, settings)
        win.open()
        root.mainloop()
