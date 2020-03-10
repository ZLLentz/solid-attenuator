import logging
from ophyd.device import Device, Component as Cpt, FormattedComponent as FCpt
from ophyd import EpicsSignal, EpicsSignalRO
import numpy as np
import h5py
from pcdsdevices.inout import TwinCATInOutPositioner

logger = logging.getLogger(__name__)


aclass HXRFilter(Device):
    """
    A single attenuation blade.

    Parameters:
    -----------
    prefix : ``str``
    filter_data : ``file``
    index : ``int``
    """
    _transmission = {} # TODO: this would be good to be dynamically set.
    # TODO: Implement an ENABLED/ALLOWED signal    

    blade = FCpt(TwinCATInOutPositioner,
                 '{prefix}:MMS:{self.index_str}', kind='normal')
    material = FCpt(EpicsSignalRO,
                    '{prefix}:FILTER:{self.index_str}:MATERIAL',
                    string=True, kind='hinted')
    thickness = FCpt(EpicsSignalRO,
                     '{prefix}:FILTER:{self.index_str}:THICKNESS',
                     kind='hinted')
    is_stuck = FCpt(EpicsSignalRO,
                     '{prefix}:FILTER:{self.index_str}:IS_STUCK',
                     kind='hinted')
    tab_whitelist = ['inserted', 'removed', 'insert', 'remove', 'transmission']
    
    def __init__(self,
                 prefix,
                 h5file=None,
                 index=None,
                 name='hxr_filter',
                 **kwargs):
        self.index_str = f'{index}'.zfill(2)
        self.index = index
        super().__init__(prefix, name=name, **kwargs)
        self.constants, self._data, self._eV_min, self._eV_inc = self.load_data(h5file)
        self.Z = self.atomic_number = int(self.constants[0]) # atomic number
        self.A = self.atomic_weight = self.constants[1] # atomic weight [g]
        self.p = self.density = self.constants[2] # density [g/cm^3]
        self.d = self.thickness.get()
        self.is_stuck = self.is_stuck()

    def load_data(self, h5file):
        """
        Loads HDF5 physics data into tables.
        """
        table = np.asarray(h5file['{}_table'.format(self.material.get())])
        constants = np.asarray(h5file['{}_constants'.format(self.material.get())])
        eV_min = table[0,0]
        eV_max = table[-1,0]
        eV_inc = (table[-1,0] - table[0,0])/len(table[:,0])
        return constants, table, eV_min, eV_inc

    def _closest_eV(self, eV):
        i = int(np.rint((eV - self._eV_min)/self._eV_inc))
        closest_eV = self._data[i,0]
        return closest_eV, i

    def get_vals(self, eV):
        """
        Return closest photon energy to eV and its transmission.
        """
        close_eV, i = self._closest_eV(eV)
        T = np.exp(-self._data[i,2]*self.d)
        return close_eV, T
    
    def transmission(self, eV):
        """
        Return beam transmission at photon energy closest ``eV``.
        """
        return self.get_vals(eV)[1]

    def inserted(self):
        """
        True if filter is inserted (in).
        """
        return self.blade.inserted

    def removed(self):
        """
        True if filter is removed (out).
        """
        return self.blade.removed
    
    @property
    def is_stuck(self):
        """
        True if filter has been set as stuck.
        Unable to move.  Hopefully retracted.
        """
        return self.is_stuck.get()

class HXRSatt(Device):
    """
    """
    cbid = None
    tab_component_names = True
    tab_whitelist = []
    
    eV = FCpt(EpicsSignalRO, '{self.eV_prefix}', kind='hinted')
    
    def __init__(self, prefix, eV_prefix="LCLS:HXR:BEAM:EV",
                 name='HXRSatt', **kwargs):
        super().__init__(prefix, name=name, **kwargs)

    def _startup(self):
        """
        Connect to PVs in order to generate
        information about filter configurations
        and photon energy.
        """
        self.N_filters = len(self.filters)
        self.config_arr = self._curr_config_arr()
        self.config_table = self._load_configs()
        self.eV_RBV = eV.get()
        self.eV.subscribe(self.eV_callback)

    def blade(self, index):
        """
        Returns the filter device at `index`.
        """
        return self.filters.get(str(index))

    def insert(self, index):
        """
        Insert filter at `index`.
        """
        inserted = self.blade(index).insert()
        self._curr_config_arr()
        return inserted
    
    def remove(self, index):
        """
        Insert filter at `index`.
        """
        removed = self.blade(index).remove()
        self._curr_config_arr()
        return removed
    
    def config(self):
        config_dict = {}
        for x in self.config_arr:
            if x == 1:
                state = 'IN'
            else:
                state = 'OUT'
            config_dict.update({str(i+1) : state})
        self.config = config_dict
        
    def _load_configs(self):
        self.config_table = self.configs['configurations']
        return self.config_table

    def _curr_config_arr(self):
        """
        Return the configuration of filter states.
        """
        config = np.ones(self.N_filters)
        for i in range(self.N_filters):
            if self.blade(i+1).inserted():
                config[i] = 1
            else:
                config[i] = np.nan
        self.config_arr = config
        return config

    def _all_transmissions(self, eV):
        """
        Calculates and returns transmission at
        photon energy ``eV`` for all non-stuck filters.
        """
        T_arr = np.ones(self.N_filters)
        for i in range(self.N_filters):
            if self.blade(i+1).is_stuck():
                T_arr = np.nan
            else:
                T_arr[i] = self.blade(i+1).transmission(eV)
        return T_arr

    def curr_transmission(self, eV):
        """
        Calculates and returns transmission at 
        photon energy ``eV`` through current filter configuration.
        """
        return np.nanprod(
            self._all_transmissions(eV)*self._curr_config())

    def eV_callback(self, value=None, **kwargs):
        """
        To be run every time the ``eV`` signal changes.
        """
        self.eV_RBV = value
        self.transmission = self.curr_transmission(value)

    def attenuate(self):
        pass

class AT2L0(HXRSatt):

    absorption_data = h5py.File('absorption_data.h5', 'r')
    configs = h5py.File('configs.h5', 'r')

    f01 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data, 
             index=1, kind='normal')
    f02 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=2, kind='normal')
    f03 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=3, kind='normal')
    f04 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=4, kind='normal')
    f05 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data, 
             index=5, kind='normal')
    f06 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=6, kind='normal')
    f07 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=7, kind='normal')
    f08 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=8, kind='normal')
    f09 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data, 
             index=9, kind='normal')
    f10 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=10, kind='normal')
    f11 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=11, kind='normal')
    f12 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=12, kind='normal')
    f13 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data, 
             index=13, kind='normal')
    f14 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=14, kind='normal')
    f15 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=15, kind='normal')
    f16 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=16, kind='normal')
    f17 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data, 
             index=17, kind='normal')
    f18 = FCpt(HXRFilter, '{prefix}', h5file=absorption_data,
             index=18, kind='normal')

    def __init__(self, prefix, name='at2l0', **kwargs):
        self.prefix = prefix
        super().__init__(prefix, name=name, **kwargs)
        self.filters = {
            str(self.f01.index) : self.f01,
            str(self.f02.index) : self.f02,
            str(self.f03.index) : self.f03,
            str(self.f04.index) : self.f04,
            str(self.f05.index) : self.f05,
            str(self.f06.index) : self.f06,
            str(self.f07.index) : self.f07,
            str(self.f08.index) : self.f08,
            str(self.f09.index) : self.f09,
            str(self.f10.index) : self.f10,
            str(self.f11.index) : self.f11,
            str(self.f12.index) : self.f12,
            str(self.f13.index) : self.f13,
            str(self.f14.index) : self.f14,
            str(self.f15.index) : self.f15,
            str(self.f16.index) : self.f16,
            str(self.f17.index) : self.f17,
            str(self.f18.index) : self.f18,
        }
        self.N_filters = len(self.filters) # temporary hacks to skip motor signals
        self.config_table = self._load_configs() #
#        super()._startup() # this will try to connect to motor signals


