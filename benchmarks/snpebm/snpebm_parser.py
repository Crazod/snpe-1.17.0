from abc import ABCMeta, abstractmethod
from pylab import *
import argparse
import json
import os

from subprocess import check_output

from snpebm_config_restrictions import *


class ArgsParser():
    def __init__(self,program_name,args_list):
        parser = argparse.ArgumentParser(prog=program_name,description="Run the {0}".format(SNPE_BENCH_NAME))
        parser._action_groups.pop()
        required = parser.add_argument_group('required arguments')
        optional = parser.add_argument_group('optional arguments')
        required.add_argument('-c', '--config_file',
                            help='Path to a valid config file', required=True)
        optional.add_argument('-o', '--output_base_dir_override',
                            help='Sets the output base directory.', required=False)
        optional.add_argument('-v', '--device_id_override',
                            help='Use this device ID instead of the one supplied in config file.  Cannot be used with -a', required=False)
        optional.add_argument('-a', '--run_on_all_connected_devices_override', action='store_true',
                              help='Runs on all connected devices, currently only support 1.  Cannot be used with -v', required=False)
        optional.add_argument('-t', '--device_os_type_override',
                            help='Specify the target OS type, valid options are %s'%CONFIG_VALID_DEVICEOSTYPES, required=False, default='android')
        optional.add_argument('-d', '--debug', action='store_true',
                            help='Set to turn on debug log', required=False)
        optional.add_argument('-s', '--sleep', type=int, default=0,
                            help='Set number of seconds to sleep between runs e.g. 20 seconds', required=False)
        optional.add_argument('-b', '--userbuffer_mode', default='',
                            help='[EXPERIMENTAL] Enable user buffer mode, default to float, can be tf8exact0', required=False)
        optional.add_argument('-p', '--perfprofile', default='high_performance',
                            help='Set the benchmark operating mode (balanced, default, sustained_high_performance, high_performance, power_saver, system_settings)', required=False)
        self._args = vars(parser.parse_args([item for item in args_list]))
        if self._args['run_on_all_connected_devices_override'] and self._args['device_id_override']:
            print('run_on_all_connected_devices_override (-a) and device_id_override (-v) are mutually exclusive')
            sys.exit(ERRNUM_PARSEARGS_ERROR)
        return

    @property
    def config_file_path(self):
        return self._args['config_file']

    @property
    def debug_enabled(self):
        return self._args['debug']

    @property
    def sleep(self):
        return self._args['sleep']

    @property
    def userbuffer_mode(self):
        return self._args['userbuffer_mode']

    @property
    def perfprofile(self):
        if self._args['perfprofile'] == None:
            return ['high_performance']
        else:
            return self._args['perfprofile']

    @property
    def output_basedir_override(self):
        return self._args['output_base_dir_override']

    @property
    def run_on_all_connected_devices_override(self):
        return self._args['run_on_all_connected_devices_override']

    @property
    def device_id_override(self):
        if self._args['device_id_override'] == None:
            return []
        else:
            return self._args['device_id_override'].split(',')

    @property
    def device_os_type_override(self):
        return self._args['device_os_type_override']

    @property
    def args(self):
        return self._args


class DataFrame:
    def __init__(self):
        self._raw_data = []

    def __iter__(self):
        return self._raw_data.__iter__()

    def add_sum(self, channel, summation, length):
        # TODO: Figure out how to avoid putting in summation as max/min just to satisfy unpacking
        self._raw_data.append([channel, summation, length, summation, summation])

    def add_sum_max_min(self, channel, summation, length, maximum, minimum):
        self._raw_data.append([channel, summation, length, maximum, minimum])

class AbstractLogParser(object):
    __metaclass__ = ABCMeta

    @abstractmethod
    def parse(self, input_dir, logger):
        return "Derived class must implement this"


class SnpeVersionParser(AbstractLogParser):
    def __init__(self, diagview_exe):
        self._diagview = diagview_exe

    def __parse_diaglog(self, diag_log_file, logger):
        try:
            diag_cmd = [self._diagview, '--input_log', diag_log_file]
            return check_output(diag_cmd).split('\n')
        except Exception as de:
            logger.warning("Failed to parse {0}".format(diag_log_file))
            logger.warning(de.message)
        return []

    def parse(self, input_dir, logger):
        assert input_dir, 'ERROR: log_file is required'
        diag_log_file = os.path.join(input_dir, SNPE_BENCH_DIAG_OUTPUT_FILE)
        diag_log_output = self.__parse_diaglog(diag_log_file, logger)
        version_str = 'unparsed'
        for data in diag_log_output:
            if 'Software library version:' in data:
                version_str = data.split(": ")[1].strip()
                break
        return version_str


class DroidTimingLogParser(AbstractLogParser):
    MAJOR_STATISTICS = {
        "Load": "Load",
        "Deserialize": "Deserialize",
        "Create": "Create",
        "Total Inference Time": "Total Inference Time",
        "Forward Propagate Time": "Forward Propagate"
    }

    def __init__(self, dnn_model, diagview_exe, dlc_info_exe):
        self._model = dnn_model
        self._diagview = diagview_exe
        self._dlc_info = dlc_info_exe

    def __parse_diaglog(self, diag_log_file, logger):
        try:
            diag_cmd = [self._diagview, '--input_log', diag_log_file]
            return check_output(diag_cmd).split('\n')
        except Exception as de:
            logger.warning("Failed to parse {0}".format(diag_log_file))
            logger.warning(de.message)
        return []

    def __parse_dlc(self, dlc_file, logger):
        try:
            layer_metadata = []
            dlc_cmd = [self._dlc_info, '-i', dlc_file]
            dlc_info_output = check_output(dlc_cmd)
            for line in dlc_info_output.split('\n'):
                if ("------------------" in line) or ("Id" in line) or ("Training" in line) or ("Concepts" in line):
                    pass
                else:
                    split_line = line.replace(" ", "").split("|")
                    if len(split_line) > 4:
                        if split_line[1].isdigit():
                            layer_metadata.append("Name:" + split_line[2] + " Type:" + split_line[3])
            return layer_metadata
        except Exception as de:
            logger.warning("Failed to parse {0}".format(dlc_file))
            logger.warning(de.message)
        return []

    def parse(self, input_dir, logger):
        def _get_layer_name(_layer_metadata, _layer_num):
            _layer_name = "layer_%03d" % _layer_num
            try:
                _layer_name += " (" + _layer_metadata[_layer_num] + ")"
            except IndexError:
                _layer_name += " (unknown)"
            return _layer_name
        assert input_dir, 'ERROR: log_file is required'
        diag_log_file = os.path.join(input_dir, SNPE_BENCH_DIAG_OUTPUT_FILE)
        dlc_file = self._model.dlc
        diag_log_output = self.__parse_diaglog(diag_log_file, logger)
        layer_metadata = self.__parse_dlc(dlc_file, logger)

        data_frame = DataFrame()
        # get forward propagate time
        for data in diag_log_output:
            for statistic_key in self.MAJOR_STATISTICS:
                if statistic_key + ":" in data:
                    statistic_time = int(data.split(": ")[1].strip().split()[0])
                    data_frame.add_sum(self.MAJOR_STATISTICS[statistic_key], statistic_time, 1)
                    break

        # get layer time
        start_collecting = False
        expected_layer_num = 0
        for data in diag_log_output:
            if "Layer Times" in data:
                start_collecting = True
            elif start_collecting is True:
                if len(data.split(": ")) >= 2:
                    layer_num = int(data.split(": ")[0])
                    layer_time = int(data.split(": ")[1].split(" ")[0])
                    # exit if over 100 "layers" between consecutive lines, from cpu_fallback
                    if layer_num > expected_layer_num + 100:
                        break
                    # insert n/a layers if there are missing ones
                    while expected_layer_num < layer_num:
                        blank_layer_time = 0
                        data_frame.add_sum(_get_layer_name(layer_metadata, expected_layer_num), blank_layer_time, 1)
                        expected_layer_num += 1
                    data_frame.add_sum(_get_layer_name(layer_metadata, layer_num), layer_time, 1)
                    expected_layer_num += 1
        return data_frame

class DroidDumpSysMemLogParser(AbstractLogParser):
    PSS = 'pss'
    PRV_DIRTY = 'prv_dirty'
    PRV_CLEAN = 'prv_clean'

    def __init__(self):
        pass

    def parse(self, input_dir, logger):
        assert input_dir, 'ERROR: log_file is required'
        log_file = os.path.join(input_dir, MEM_LOG_FILE_NAME)
        fid = open(log_file, 'r')
        key_word = 'TOTAL'

        # to get around addn information being dumped in Android M
        skip_key_word = 'TOTAL:'
        pss = []
        prv_dirty = []
        prv_clean = []

        logger.debug('Parsing Memory Log: %s' % log_file)
        for line in fid.readlines():
            tmp = line.strip().split()
            if (key_word in tmp) and not (skip_key_word in tmp):
                _pss = int(tmp[1])
                _prv_dirty = int(tmp[2])
                _prv_clean = int(tmp[3])
                if _pss == 0 and _prv_dirty == 0 and _prv_clean == 0:
                    continue
                pss.append(int(tmp[1]))
                prv_dirty.append(int(tmp[2]))
                prv_clean.append(int(tmp[3]))

        data_frame = DataFrame()
        if len(pss) == 0 and len(prv_dirty) == 0 and len(prv_clean) == 0:
            logger.warning('No memory info found in %s' % log_file)
        else:
	    data_frame.add_sum_max_min(self.PSS, sum(pss), len(pss), max(pss), min(pss))
	    data_frame.add_sum_max_min(self.PRV_DIRTY, sum(prv_dirty), len(prv_dirty), max(prv_dirty), min(prv_dirty))
	    data_frame.add_sum_max_min(self.PRV_CLEAN, sum(prv_clean), len(prv_clean), max(prv_clean), min(prv_clean))
        return data_frame

class LeDumpSysMemLogParser(AbstractLogParser):
    PSS = 'pss'
    PRV_DIRTY = 'prv_dirty'
    PRV_CLEAN = 'prv_clean'

    def __init__(self):
        pass

    def parse(self, input_dir, logger):
        assert input_dir, 'log_file is required'
        log_file = os.path.join(input_dir, MEM_LOG_FILE_NAME)
        fid = open(log_file, 'r')

        pss = []
        prv_dirty = []
        prv_clean = []

        seperate_key_word = '===='
        pss_keyword = "Pss:"
        pd_keyword = "Private_Dirty:"
        pc_keyword = "Private_Clean:"

        _pss = 0
        _pd = 0
        _pc = 0

        logger.debug('Parsing Memory Log: %s' % log_file)
        for line in fid.readlines():
            tmp = line.strip().split()
            if(seperate_key_word in tmp):
                if(_pss <> 0):
                    pss.append(_pss)
                    _pss = 0
                if(_pd <> 0):
                    prv_dirty.append(_pd)
                    _pd = 0
                if(_pc <> 0):
                    prv_clean.append(_pc)
                    _pc = 0
            if (pss_keyword in tmp):
                _pss += int(tmp[1])
            if (pd_keyword in tmp):
                _pd += int(tmp[1])
            if (pc_keyword in tmp):
                _pc += int(tmp[1])

        fid.close()
        data_frame = DataFrame()
        if len(pss) == 0 and len(prv_dirty) == 0 and len(prv_clean) == 0:
            logger.warning('No memory info found in %s' % log_file)
        else:
            data_frame.add_sum_max_min(self.PSS, sum(pss), len(pss), max(pss), min(pss))
            data_frame.add_sum_max_min(self.PRV_DIRTY, sum(prv_dirty), len(prv_dirty), max(prv_dirty), min(prv_dirty))
            data_frame.add_sum_max_min(self.PRV_CLEAN, sum(prv_clean), len(prv_clean), max(prv_clean), min(prv_clean))
        return data_frame

class MonsoonPowerLogParser(AbstractLogParser):
    CURRENT_A = 'currentA'

    def __init__(self):
        self._samples = None

    def parse(self, input_dir, logger):
        assert input_dir, 'ERROR: log_file is required'
        log_file = os.path.join(input_dir, POWER_LOG_FILE_NAME)
        fid = open(log_file, 'r')
        with fid:
            fid.seek(0)
            logger.debug("Parsing power log: {0}".format(log_file))
            powermonitor_output = fid.read()
        assert powermonitor_output, 'PowerMonitor produced no output'
        power_data = json.loads(powermonitor_output)
        self._samples = power_data['samples']
        data_frame = DataFrame()
        current_sum = 0
        current_max = 0
        current_min = 0
        for (currentA, voltageV) in self._samples:
            current_sum += currentA
            current_max = max(current_max, currentA)
            current_min = min(current_min, currentA)
        data_frame.add_sum_max_min(self.CURRENT_A, current_sum, len(self._samples), current_max, current_min)
        return data_frame

    def plot(self, input_dir, logger):
        if self._samples is not None:
            plot_data = []
            for (currentA, voltageV) in self._samples:
                plot_data.append(currentA)
            plot(plot_data)
            ylabel('current A')
            plot_file = os.path.join(input_dir, POWER_LOG_PLOT_NAME)
            logger.info("Generating power plot: {0}".format(plot_file))
            savefig(plot_file)
            clf()

class EmptyLogParser(AbstractLogParser):
    def __init__(self):
        pass

    def parse(self, input_dir, logger):
        return DataFrame()

class LogParserFactory:
    def __init__(self):
        pass

    @staticmethod
    def make_parser(measure, config):
        if measure == MEASURE_MEM:
            if (config.platform == PLATFORM_OS_ANDROID):
                return DroidDumpSysMemLogParser()
            elif(config.platform == PLATFORM_OS_LINUX):
                return LeDumpSysMemLogParser()
            else:
                raise Exception("make_parser: Invalid platform !!!", config.platform)
        elif measure == MEASURE_POWER:
            return MonsoonPowerLogParser()
        elif measure == MEASURE_ACCURACY:
            return EmptyLogParser()
        elif measure == MEASURE_TIMING:
            return DroidTimingLogParser(config.dnn_model,
                                        config.host_artifacts[SNPE_DIAGVIEW_EXE],
                                        config.host_artifacts[SNPE_DLC_INFO_EXE])
        elif measure == MEASURE_SNPE_VERSION:
            return SnpeVersionParser(config.host_artifacts[SNPE_DIAGVIEW_EXE])