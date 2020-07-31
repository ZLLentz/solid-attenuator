import threading

from caproto import AlarmSeverity, AlarmStatus, ChannelType
from caproto.asyncio.server import AsyncioAsyncLayer
from caproto.server import PVGroup, pvproperty
from caproto.server.autosave import autosaved

from .. import calculator
from . import util
from .util import monitor_pvs

STATE_FROM_MOTOR = {
    0: 0,  # unknown -> out
    1: 0,  # out
    2: 1,  # in
}

STATE_TO_MOTOR = {
    0: 1,  # out
    1: 2,  # in
}


class SystemGroup(PVGroup):
    """
    PV group for attenuator system-spanning information.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # TODO: this could be done by wrapping SystemGroup
        for obj in (self.best_config, self.active_config):
            util.hack_max_length_of_channeldata(obj,
                                                [0] * self.parent.num_filters)

        # TODO: caproto does not make this easy. We explicitly will be using
        # asyncio here.
        self.async_lib = AsyncioAsyncLayer()
        self._context = {}
        self._pv_put_queue = None
        self._put_thread = None

    calculated_transmission = pvproperty(
        value=0.1,
        name='T_CALC',
        record='ao',
        upper_alarm_limit=1.0,
        lower_alarm_limit=0.0,
        read_only=True,
        doc='Calculated transmission (all blades)',
        precision=3,
    )

    calculated_transmission_3omega = pvproperty(
        name='T_3OMEGA',
        value=0.5,
        upper_alarm_limit=1.0,
        lower_alarm_limit=0.0,
        read_only=True,
        doc='Calculated 3omega transmission (all blades)',
        precision=3,
    )

    best_config_error = pvproperty(
        value=0.1,
        name='BestConfigError_RBV',
        record='ao',
        upper_alarm_limit=1.0,
        lower_alarm_limit=-1.0,
        read_only=True,
        doc='Calculated transmission error',
        precision=3,
    )

    running = pvproperty(
        value='False',
        name='Running',
        record='bo',
        enum_strings=['False', 'True'],
        read_only=True,
        doc='The system is running',
        dtype=ChannelType.ENUM
    )

    mirror_in = pvproperty(
        value='False',
        name='MIRROR_IN',
        record='bo',
        enum_strings=['False', 'True'],
        read_only=True,
        doc='The inspection mirror is in',
        dtype=ChannelType.ENUM
    )

    calc_mode = pvproperty(
        value='Floor',
        name='CalcMode',
        record='bo',
        enum_strings=['Floor', 'Ceiling'],
        read_only=False,
        doc='Mode for selecting floor or ceiling transmission estimation',
        dtype=ChannelType.ENUM
    )

    energy_source = pvproperty(
        value='Actual',
        name='EnergySource',
        record='bo',
        enum_strings=['Actual', 'Custom'],
        read_only=False,
        doc='Choose the source of photon energy',
        dtype=ChannelType.ENUM,
    )

    best_config = pvproperty(
        name='BestConfiguration_RBV',
        value=0,
        max_length=1,
        read_only=True
    )

    active_config = pvproperty(
        name='ActiveConfiguration_RBV',
        value=0,
        max_length=1,
        read_only=True,
        alarm_group='motors',
    )

    energy_actual = pvproperty(
        name='ActualPhotonEnergy_RBV',
        value=0.0,
        read_only=True,
        units='eV',
        alarm_group='valid_photon_energy',
        precision=1,
    )

    energy_custom = pvproperty(
        name='CustomPhotonEnergy',
        value=0.0,
        read_only=False,
        units='eV',
        lower_ctrl_limit=100.0,
        upper_ctrl_limit=30000.0,
        precision=1,
    )

    energy_calc = pvproperty(
        name='LastPhotonEnergy_RBV',
        value=0.0,
        read_only=True,
        units='eV',
        doc='Energy that was used for the calculation.',
        precision=1,
    )

    apply_config = pvproperty(
        name='ApplyConfiguration',
        value='False',
        record='bo',
        enum_strings=['False', 'True'],
        doc='Apply the calculated configuration.',
        dtype=ChannelType.ENUM,
        alarm_group='motors',
    )

    desired_transmission = autosaved(
        pvproperty(
            name='DesiredTransmission',
            value=0.5,
            lower_ctrl_limit=0.0,
            upper_ctrl_limit=1.0,
            doc='Desired transmission value',
            precision=3,
        )
    )

    run = pvproperty(
        value='False',
        name='Run',
        record='bo',
        enum_strings=['False', 'True'],
        doc='Run calculation',
        dtype=ChannelType.ENUM
    )

    @active_config.startup
    async def active_config(self, instance, async_lib):
        motor_pvnames = self.parent.monitor_pvnames['motors']
        monitor_list = sum((pvlist for pvlist in motor_pvnames.values()),
                           [])
        all_status = {pv: False for pv in monitor_list}

        async def update_connection_status(pv, status):
            all_status[pv] = (status == 'connected')
            if all(all_status.values()):
                status, severity = AlarmStatus.NO_ALARM, AlarmSeverity.NO_ALARM
            else:
                status, severity = AlarmStatus.LINK, AlarmSeverity.MAJOR_ALARM

            if instance.alarm.status != status:
                await instance.alarm.write(status=status, severity=severity)

        async for event, context, data in monitor_pvs(*monitor_list,
                                                      async_lib=async_lib):
            if event == 'connection':
                await update_connection_status(context.name, data)
                continue

            value = data.data[0]
            pvname = context.pv.name
            if pvname in motor_pvnames['get']:
                idx = motor_pvnames['get'].index(pvname)
                new_config = list(self.active_config.value)
                new_config[idx] = STATE_FROM_MOTOR.get(value, value)
                if tuple(new_config) != tuple(self.active_config.value):
                    self.log.info('Active config changed: %s', new_config)
                    await self.active_config.write(new_config)

    @energy_actual.startup
    async def energy_actual(self, instance, async_lib):
        """Update beam energy and calculated values."""
        async def update_connection_status(status):
            if status == 'connected':
                status, severity = AlarmStatus.NO_ALARM, AlarmSeverity.NO_ALARM
            else:
                status, severity = AlarmStatus.LINK, AlarmSeverity.MAJOR_ALARM
            await instance.alarm.write(status=status, severity=severity)

        await update_connection_status('disconnected')
        pvname = self.parent.monitor_pvnames['ev']
        async for event, context, data in monitor_pvs(pvname,
                                                      async_lib=async_lib):
            if event == 'connection':
                self.log.info('%s %s', context, data)
                await update_connection_status(data)
                continue

            eV = data.data[0]
            self.log.debug('Photon energy changed: %s', eV)

            if instance.value != eV:
                delta = instance.value - eV
                if abs(delta) > 1000:
                    self.log.info("Photon energy changed to %s eV.", eV)
                await instance.write(eV)

        return eV

    @util.block_on_reentry()
    async def run_calculation(self):
        energy = {
            'Actual': self.energy_actual.value,
            'Custom': self.energy_custom.value,
        }[self.energy_source.value]

        await self.energy_calc.write(energy)

        # Update all of the filters first, to determine their transmission
        # at this energy
        for filter in self.parent.filters.values():
            await filter.set_photon_energy(energy)

        await self.calculated_transmission.write(
            self.parent.calculate_transmission()
        )
        await self.calculated_transmission_3omega.write(
            self.parent.calculate_transmission_3omega()
        )

        # Using the above-calculated transmissions, find the best configuration
        config = calculator.get_best_config(
            all_transmissions=self.parent.all_transmissions,
            t_des=self.desired_transmission.value,
            mode=self.calc_mode.value
        )
        await self.best_config.write(config.filter_states)
        await self.best_config_error.write(
            config.transmission - self.desired_transmission.value
        )
        self.log.info(
            'Energy %s eV %s transmission desired %.2g estimated %.2g '
            '(delta %.3g) configuration: %s',
            energy,
            self.calc_mode.value,
            self.desired_transmission.value,
            config.transmission,
            self.best_config_error.value,
            config.filter_states,
        )

    @run.putter
    async def run(self, instance, value):
        if value == 'False':
            return

        try:
            await self.run_calculation()
        except Exception:
            self.log.exception('update_config failed?')

    # RUN.PROC -> run = 1
    util.process_writes_value(run, value=1)

    @apply_config.startup
    async def apply_config(self, instance, async_lib):
        def put_thread():
            while True:
                pv, value = self._pv_put_queue.get()
                try:
                    pv.write([value])
                except Exception:
                    self.log.exception('Failed to put value: %s=%s', pv, value)

        ctx = util.get_default_thread_context()

        self._set_pvs = ctx.get_pvs(
            *self.parent.monitor_pvnames['motors']['set'], timeout=None)

        self._pv_put_queue = self.async_lib.ThreadsafeQueue()
        self._put_thread = threading.Thread(target=put_thread, daemon=True)
        self._put_thread.start()

    @apply_config.putter
    async def apply_config(self, instance, value):
        if value == 'False':
            return

        for set_pv, value in zip(self._set_pvs, self.best_config.value):
            await self._pv_put_queue.async_put(
                (set_pv, STATE_TO_MOTOR.get(value, value)))

    # apply_config.PROC -> apply_config = 1
    util.process_writes_value(apply_config, value=1)
