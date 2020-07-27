import numpy as np
from caproto.server import PVGroup

from .db.filters import FilterGroup
from .db.system import SystemGroup


class IOCMain(PVGroup):
    """
    """

    def __init__(self, prefix, *, filters, groups, eV, pmps_run, pmps_tdes,
                 **kwargs):
        super().__init__(prefix, **kwargs)
        self.prefix = prefix
        self.filters = filters
        self.groups = groups
        self.monitor_pvnames = dict(
            ev=eV,
            pmps_run=pmps_run,
            pmps_tdes=pmps_tdes,
        )

    @property
    def working_filters(self):
        """
        Returns a dictionary of all filters that are in working order

        That is to say, filters that are not stuck.
        """
        return {
            idx: filt for idx, filt in self.filters.items()
            if filt.is_stuck.value != "True"
        }

    def t_calc(self):
        """
        Total transmission through all filter blades.

        Stuck blades are assumed to be 'OUT' and thus the total transmission
        will be overestimated (in the case any blades are actually stuck 'IN').
        """
        t = 1.
        for filt in self.working_filters.values():
            t *= filt.transmission.value
        return t

    def t_calc_3omega(self):
        """
        Total 3rd harmonic transmission through all filter blades.

        Stuck blades are assumed to be 'OUT' and thus the total transmission
        will be overestimated (in the case any blades are actually stuck 'IN').
        """
        t = 1.
        for filt in self.working_filters.values():
            t *= filt.transmission_3omega.value
        return t

    @property
    def all_transmissions(self):
        """
        Return an array of the transmission values for each filter at the
        current photon energy.

        Stuck filters get a transmission of NaN, which omits them from
        calculations/considerations.
        """
        T_arr = np.zeros(len(self.filters)) * np.nan
        for idx, filt in self.working_filters.items():
            T_arr[idx - 1] = filt.transmission.value
        return T_arr

    def _get_config(self, T_des=None):
        """
        Return the optimal floor (lower than desired transmission) or ceiling
        (higher than desired transmission) configuration based on the current
        mode setting.
        """
        if not T_des:
            T_des = self.sys.t_desired.value
        mode = self.sys.mode.value

        conf = self.find_configs()
        config_bestLow, config_bestHigh, T_bestLow, T_bestHigh = conf

        if mode == "Floor":
            return config_bestLow, T_bestLow, T_des
        return config_bestHigh, T_bestHigh, T_des


def create_ioc(prefix,
               *,
               eV_pv,
               pmps_run_pv,
               pmps_tdes_pv,
               filter_group,
               **ioc_options):
    """IOC Setup."""
    groups = {}
    filters = {}
    ioc = IOCMain(prefix=prefix,
                  filters=filters,
                  groups=groups,
                  eV=eV_pv,
                  pmps_run=pmps_run_pv,
                  pmps_tdes=pmps_tdes_pv,
                  **ioc_options)

    for index, group_prefix in filter_group.items():
        filt = FilterGroup(f'{prefix}:FILTER:{group_prefix}:', ioc=ioc,
                           index=index)
        ioc.filters[index] = filt
        ioc.groups[group_prefix] = filt

    ioc.groups['SYS'] = SystemGroup(f'{prefix}:SYS:', ioc=ioc)
    ioc.sys = ioc.groups['SYS']

    for group in ioc.groups.values():
        ioc.pvdb.update(**group.pvdb)

    return ioc
