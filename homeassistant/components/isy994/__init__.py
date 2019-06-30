"""Support the ISY-994 controllers."""
from collections import namedtuple
import logging
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.components.binary_sensor import (
    DEVICE_CLASSES_SCHEMA as BINARY_SENSOR_DCS)
from homeassistant.components.sensor import DEVICE_CLASSES_SCHEMA as SENSOR_DCS
from homeassistant.const import (
    CONF_BINARY_SENSORS, CONF_DEVICE_CLASS, CONF_HOST, CONF_ICON, CONF_ID,
    CONF_NAME, CONF_PASSWORD, CONF_SENSORS, CONF_SWITCHES, CONF_TYPE,
    CONF_UNIT_OF_MEASUREMENT, CONF_USERNAME, EVENT_HOMEASSISTANT_STOP,
    STATE_OFF, STATE_ON)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import ConfigType, Dict

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'isy994'

CONF_IGNORE_STRING = 'ignore_string'
CONF_SENSOR_STRING = 'sensor_string'
CONF_ENABLE_CLIMATE = 'enable_climate'
CONF_ISY_VARIABLES = 'isy_variables'
CONF_TLS_VER = 'tls'

DEFAULT_IGNORE_STRING = '{IGNORE ME}'
DEFAULT_SENSOR_STRING = 'sensor'

DEFAULT_ON_VALUE = 1
DEFAULT_OFF_VALUE = 0

KEY_ACTIONS = 'actions'
KEY_FOLDER = 'folder'
KEY_MY_PROGRAMS = 'My Programs'
KEY_STATUS = 'status'

VAR_BASE_SCHEMA = vol.Schema({
    vol.Required(CONF_ID): cv.positive_int,
    vol.Required(CONF_TYPE): vol.All(cv.positive_int,
                                     vol.In([1, 2])),
    vol.Optional(CONF_ICON): cv.icon,
    vol.Optional(CONF_NAME): cv.string,
    })

SENSOR_VAR_SCHEMA = VAR_BASE_SCHEMA.extend({
    vol.Optional(CONF_DEVICE_CLASS): SENSOR_DCS,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
    })

BINARY_SENSOR_VAR_SCHEMA = VAR_BASE_SCHEMA.extend({
    vol.Optional(CONF_DEVICE_CLASS): BINARY_SENSOR_DCS,
    vol.Optional(STATE_ON, default=DEFAULT_ON_VALUE): vol.Coerce(int),
    vol.Optional(STATE_OFF, default=DEFAULT_OFF_VALUE): vol.Coerce(int),
    })

SWITCH_VAR_SCHEMA = VAR_BASE_SCHEMA.extend({
    vol.Optional(STATE_ON, default=DEFAULT_ON_VALUE): vol.Coerce(int),
    vol.Optional(STATE_OFF, default=DEFAULT_OFF_VALUE): vol.Coerce(int),
    })

ISY_VARIABLES_SCHEMA = vol.Schema({
    vol.Optional(CONF_SENSORS, default=[]):
        vol.All(cv.ensure_list, [SENSOR_VAR_SCHEMA]),
    vol.Optional(CONF_BINARY_SENSORS, default=[]):
        vol.All(cv.ensure_list, [BINARY_SENSOR_VAR_SCHEMA]),
    vol.Optional(CONF_SWITCHES, default=[]):
        vol.All(cv.ensure_list, [SWITCH_VAR_SCHEMA]),
    })

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.url,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_TLS_VER): vol.Coerce(float),
        vol.Optional(CONF_IGNORE_STRING,
                     default=DEFAULT_IGNORE_STRING): cv.string,
        vol.Optional(CONF_SENSOR_STRING,
                     default=DEFAULT_SENSOR_STRING): cv.string,
        vol.Optional(CONF_ENABLE_CLIMATE, default=True): cv.boolean,
        vol.Optional(CONF_ISY_VARIABLES, default={}): ISY_VARIABLES_SCHEMA
    })
}, extra=vol.ALLOW_EXTRA)

# Do not use the Hass consts for the states here - we're matching exact API
# responses, not using them for Hass states
# Z-Wave Categories: https://www.universal-devices.com/developers/
#                      wsdk/5.0.4/4_fam.xml
NODE_FILTERS = {
    'binary_sensor': {
        'uom': [],
        'states': [],
        'node_def_id': ['BinaryAlarm', 'OnOffControl_ADV'],
        'insteon_type': ['7.13.', '16.'],  # Does a startswith() match; incl .
        'zwave_cat': (['104', '112', '138'] +
                      list(map(str, range(148, 179))))
    },
    'sensor': {
        # This is just a more-readable way of including MOST uoms between 1-100
        # (Remember that range() is non-inclusive of the stop value)
        'uom': (['1'] +
                list(map(str, range(3, 11))) +
                list(map(str, range(12, 51))) +
                list(map(str, range(52, 66))) +
                list(map(str, range(69, 78))) +
                ['79'] +
                list(map(str, range(82, 97)))),
        'states': [],
        'node_def_id': ['IMETER_SOLO'],
        'insteon_type': ['9.0.', '9.7.'],
        'zwave_cat': (['118'] +
                      list(map(str, range(180, 184))))
    },
    'lock': {
        'uom': ['11'],
        'states': ['locked', 'unlocked'],
        'node_def_id': ['DoorLock'],
        'insteon_type': ['15.'],
        'zwave_cat': ['111']
    },
    'fan': {
        'uom': [],
        'states': ['off', 'low', 'med', 'high'],
        'node_def_id': ['FanLincMotor'],
        'insteon_type': ['1.46.'],
        'zwave_cat': []
    },
    'cover': {
        'uom': ['97'],
        'states': ['open', 'closed', 'closing', 'opening', 'stopped'],
        'node_def_id': [],
        'insteon_type': [],
        'zwave_cat': []
    },
    'light': {
        'uom': ['51'],
        'states': ['on', 'off', '%'],
        'node_def_id': ['DimmerLampSwitch', 'DimmerLampSwitch_ADV',
                        'DimmerSwitchOnly', 'DimmerSwitchOnly_ADV',
                        'DimmerLampOnly', 'BallastRelayLampSwitch',
                        'BallastRelayLampSwitch_ADV',
                        'RemoteLinc2', 'RemoteLinc2_ADV'],
        'insteon_type': ['1.'],
        'zwave_cat': ['109', '119']
    },
    'switch': {
        'uom': ['2', '78'],
        'states': ['on', 'off'],
        'node_def_id': ['OnOffControl', 'RelayLampSwitch',
                        'RelayLampSwitch_ADV', 'RelaySwitchOnlyPlusQuery',
                        'RelaySwitchOnlyPlusQuery_ADV', 'RelayLampOnly',
                        'RelayLampOnly_ADV', 'KeypadButton',
                        'KeypadButton_ADV', 'EZRAIN_Input', 'EZRAIN_Output',
                        'EZIO2x4_Input', 'EZIO2x4_Input_ADV', 'BinaryControl',
                        'BinaryControl_ADV', 'AlertModuleSiren',
                        'AlertModuleSiren_ADV', 'AlertModuleArmed', 'Siren',
                        'Siren_ADV'],
        'insteon_type': ['2.', '9.10.', '9.11.'],
        'zwave_cat': ['121', '122', '123', '137', '141', '147']
    },
    'climate': {
        'uom': ['2'],
        'states': ['heating', 'cooling', 'idle', 'fan_only', 'off'],
        'node_def_id': ['TempLinc', 'Thermostat'],
        'insteon_type': ['5.'],
        'zwave_cat': ['140']
    }
}

SUPPORTED_DOMAINS = ['binary_sensor', 'sensor', 'lock', 'fan', 'cover',
                     'light', 'switch', 'climate']
SUPPORTED_PROGRAM_DOMAINS = ['binary_sensor', 'lock', 'fan', 'cover', 'switch']
SUPPORTED_VARIABLE_DOMAINS = ['binary_sensor', 'sensor', 'switch']

# ISY Scenes are more like Switches than Hass Scenes
# (they can turn off, and report their state)
SCENE_DOMAIN = 'switch'

ISY994_NODES = "isy994_nodes"
ISY994_WEATHER = "isy994_weather"
ISY994_PROGRAMS = "isy994_programs"
ISY994_VARIABLES = "isy994_variables"

ISY994_EVENT_FRIENDLY_NAME = {
    "OL": "On Level",
    "RR": "Ramp Rate",
    "CLISPH": "Heat Setpoint",
    "CLISPC": "Cool Setpoint",
    "CLIFS": "Fan State",
    "CLIMD": "Thermostat Mode",
    "CLIHUM": "Humidity",
    "CLIHCS": "Heat/Cool State",
    "CLIEMD": "Energy Saving Mode",
    "ERR": "Device communication errors",
    "UOM": "Unit of Measure",
    "TPW": "Total kW Power",
    "PPW": "Polarized Power",
    "PF": "Power Factor",
    "CC": "Current",
    "CV": "Voltage",
    "AIRFLOW": "Air Flow",
    "ALARM": "Alarm",
    "ANGLE": "Angle Position",
    "ATMPRES": "Atmospheric Pressure",
    "BARPRES": "Barometric Pressure",
    "BATLVL": "Battery Level",
    "CLIMD": "Mode",
    "CLISMD": "Schedule Mode",
    "CLITEMP": "Temperature",
    "CO2LVL": "CO2 Level",
    "CPW": "Power",
    "DISTANC": "Distance",
    "ELECRES": "Electrical Resistivity",
    "ELECCON": "Electrical Conductivity",
    "GPV": "General Purpose",
    "GVOL": "Gas Volume",
    "LUMIN": "Luminance",
    "MOIST": "Moisture",
    "PCNT": "Pulse Count",
    "PULSCNT": "Pulse Count",
    "RAINRT": "Rain Rate",
    "ROTATE": "Rotation",
    "SEISINT": "Seismic Intensity",
    "SEISMAG": "Seismic Magnitude",
    "SOLRAD": "Solar Radiation",
    "SPEED": "Speed",
    "SVOL": "Sound Volume",
    "TANKCAP": "Tank Capacity",
    "TIDELVL": "Tide Level",
    "TIMEREM": "Time Remaining",
    "UAC": "User Number",
    "UV": "UV Light",
    "USRNUM": "User Number",
    "VOCLVL": "VOC Level",
    "WEIGHT": "Weight",
    "WINDDIR": "Wind Direction",
    "WVOL": "Water Volume"
}

ISY994_EVENT_IGNORE = ['DON', 'ST', 'DFON', 'DOF', 'DFOF', 'BEEP', 'RESET',
                       'X10', 'BMAN', 'SMAN', 'BRT', 'DIM', 'BUSY']

WeatherNode = namedtuple('WeatherNode', ('status', 'name', 'uom'))


def _check_for_node_def(hass: HomeAssistant, node,
                        single_domain: str = None) -> bool:
    """Check if the node matches the node_def_id for any domains.

    This is only present on the 5.0 ISY firmware, and is the most reliable
    way to determine a device's type.
    """
    if not hasattr(node, 'node_def_id') or node.node_def_id is None:
        # Node doesn't have a node_def (pre 5.0 firmware most likely)
        return False

    node_def_id = node.node_def_id

    domains = SUPPORTED_DOMAINS if not single_domain else [single_domain]
    for domain in domains:
        if node_def_id in NODE_FILTERS[domain]['node_def_id']:
            hass.data[ISY994_NODES][domain].append(node)
            return True

    return False


def _check_for_insteon_type(hass: HomeAssistant, node,
                            single_domain: str = None) -> bool:
    """Check if the node matches the Insteon type for any domains.

    This is for (presumably) every version of the ISY firmware, but only
    works for Insteon device. "Node Server" (v5+) and Z-Wave and others will
    not have a type.
    """
    if not hasattr(node, 'type') or node.type is None:
        # Node doesn't have a type (non-Insteon device most likely)
        return False

    device_type = node.type
    domains = SUPPORTED_DOMAINS if not single_domain else [single_domain]
    for domain in domains:
        if any([device_type.startswith(t) for t in
                set(NODE_FILTERS[domain]['insteon_type'])]):

            # Hacky special-case just for FanLinc, which has a light module
            # as one of its nodes. Note that this special-case is not necessary
            # on ISY 5.x firmware as it uses the superior NodeDefs method
            if domain == 'fan' and int(node.nid[-1]) == 1:
                hass.data[ISY994_NODES]['light'].append(node)
                return True

            # Hacky special-case just for Thermostats, which has a "Heat" and
            # "Cool" sub-node on address 2 and 3
            if domain == 'climate' and int(node.nid[-1]) in [2, 3]:
                hass.data[ISY994_NODES]['binary_sensor'].append(node)
                return True

            hass.data[ISY994_NODES][domain].append(node)
            return True

    return False


def _check_for_zwave_cat(hass: HomeAssistant, node,
                         single_domain: str = None) -> bool:
    """Check if the node matches the ISY Z-Wave Category for any domains.

    This is for (presumably) every version of the ISY firmware, but only
    works for Z-Wave Devices with the devtype.cat property.
    """
    if not hasattr(node, 'devtype_cat') or node.devtype_cat is None:
        # Node doesn't have a device type category (non-Z-Wave device)
        return False

    device_type = node.devtype_cat
    domains = SUPPORTED_DOMAINS if not single_domain else [single_domain]
    for domain in domains:
        if any([device_type.startswith(t) for t in
                set(NODE_FILTERS[domain]['zwave_cat'])]):

            hass.data[ISY994_NODES][domain].append(node)
            return True

    return False


def _check_for_uom_id(hass: HomeAssistant, node,
                      single_domain: str = None,
                      uom_list: list = None) -> bool:
    """Check if a node's uom matches any of the domains uom filter.

    This is used for versions of the ISY firmware that report uoms as a single
    ID. We can often infer what type of device it is by that ID.
    """
    if not hasattr(node, 'uom') or node.uom is None:
        # Node doesn't have a uom (Scenes for example)
        return False

    node_uom = set(map(str.lower, node.uom))

    if uom_list:
        if node_uom.intersection(uom_list):
            hass.data[ISY994_NODES][single_domain].append(node)
            return True
    else:
        domains = SUPPORTED_DOMAINS if not single_domain else [single_domain]
        for domain in domains:
            if node_uom.intersection(NODE_FILTERS[domain]['uom']):
                hass.data[ISY994_NODES][domain].append(node)
                return True

    return False


def _check_for_states_in_uom(hass: HomeAssistant, node,
                             single_domain: str = None,
                             states_list: list = None) -> bool:
    """Check if a list of uoms matches two possible filters.

    This is for versions of the ISY firmware that report uoms as a list of all
    possible "human readable" states. This filter passes if all of the possible
    states fit inside the given filter.
    """
    if not hasattr(node, 'uom') or node.uom is None:
        # Node doesn't have a uom (Scenes for example)
        return False

    node_uom = set(map(str.lower, node.uom))

    if states_list:
        if node_uom == set(states_list):
            hass.data[ISY994_NODES][single_domain].append(node)
            return True
    else:
        domains = SUPPORTED_DOMAINS if not single_domain else [single_domain]
        for domain in domains:
            if node_uom == set(NODE_FILTERS[domain]['states']):
                hass.data[ISY994_NODES][domain].append(node)
                return True

    return False


def _is_sensor_a_binary_sensor(hass: HomeAssistant, node) -> bool:
    """Determine if the given sensor node should be a binary_sensor."""
    if _check_for_node_def(hass, node, single_domain='binary_sensor'):
        return True
    if _check_for_insteon_type(hass, node, single_domain='binary_sensor'):
        return True

    # For the next two checks, we're providing our own set of uoms that
    # represent on/off devices. This is because we can only depend on these
    # checks in the context of already knowing that this is definitely a
    # sensor device.
    if _check_for_uom_id(hass, node, single_domain='binary_sensor',
                         uom_list=['2', '78']):
        return True
    if _check_for_states_in_uom(hass, node, single_domain='binary_sensor',
                                states_list=['on', 'off']):
        return True

    return False


def _categorize_nodes(hass: HomeAssistant, nodes, ignore_identifier: str,
                      sensor_identifier: str) -> None:
    """Sort the nodes to their proper domains."""
    for (path, node) in nodes:
        ignored = ignore_identifier in path or ignore_identifier in node.name
        if ignored:
            # Don't import this node as a device at all
            continue

        from PyISY.Nodes import Group
        if isinstance(node, Group):
            hass.data[ISY994_NODES][SCENE_DOMAIN].append(node)
            continue

        if sensor_identifier in path or sensor_identifier in node.name:
            # User has specified to treat this as a sensor. First we need to
            # determine if it should be a binary_sensor.
            if _is_sensor_a_binary_sensor(hass, node):
                continue
            else:
                hass.data[ISY994_NODES]['sensor'].append(node)
                continue

        # We have a bunch of different methods for determining the device type,
        # each of which works with different ISY firmware versions or device
        # family. The order here is important, from most reliable to least.
        if _check_for_node_def(hass, node):
            continue
        if _check_for_insteon_type(hass, node):
            continue
        if _check_for_zwave_cat(hass, node):
            continue
        if _check_for_uom_id(hass, node):
            continue
        if _check_for_states_in_uom(hass, node):
            continue


def _categorize_programs(hass: HomeAssistant, programs: dict) -> None:
    """Categorize the ISY994 programs."""
    for domain in SUPPORTED_PROGRAM_DOMAINS:
        try:
            folder = programs[KEY_MY_PROGRAMS]['HA.{}'.format(domain)]
        except KeyError:
            pass
        else:
            for dtype, _, node_id in folder.children:
                if dtype != KEY_FOLDER:
                    continue
                entity_folder = folder[node_id]
                try:
                    status = entity_folder[KEY_STATUS]
                    assert status.dtype == 'program', 'Not a program'
                    if domain != 'binary_sensor':
                        actions = entity_folder[KEY_ACTIONS]
                        assert actions.dtype == 'program', 'Not a program'
                    else:
                        actions = None
                except (AttributeError, KeyError, AssertionError):
                    _LOGGER.warning("Program entity '%s' not loaded due "
                                    "to invalid folder structure.",
                                    entity_folder.name)
                    continue

                entity = (entity_folder.name, status, actions)
                hass.data[ISY994_PROGRAMS][domain].append(entity)


def _categorize_variables(hass: HomeAssistant, variables: dict,
                          domain_cfg: dict, domain: str) -> None:
    """Categorize the ISY994 Variables."""
    if domain_cfg is None:
        return
    for isy_var in domain_cfg:
        vid = isy_var.get(CONF_ID)
        vtype = isy_var.get(CONF_TYPE)
        _, vname, _ = next((var for i, var in
                            enumerate(variables[vtype].children)
                            if var[2] == vid), None)
        if vname is None:
            _LOGGER.error("ISY Variable Not Found in ISY List; "
                          "check your config for Variable %s.%s",
                          vtype, vid)
            continue
        variable = (isy_var, vname, variables[vtype][vid])
        hass.data[ISY994_VARIABLES][domain].append(variable)


def _categorize_weather(hass: HomeAssistant, climate) -> None:
    """Categorize the ISY994 weather data."""
    climate_attrs = dir(climate)
    weather_nodes = [WeatherNode(getattr(climate, attr),
                                 attr.replace('_', ' '),
                                 getattr(climate, '{}_units'.format(attr)))
                     for attr in climate_attrs
                     if '{}_units'.format(attr) in climate_attrs]
    hass.data[ISY994_WEATHER].extend(weather_nodes)


def setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the ISY 994 platform."""
    hass.data[ISY994_NODES] = {}
    for domain in SUPPORTED_DOMAINS:
        hass.data[ISY994_NODES][domain] = []

    hass.data[ISY994_WEATHER] = []

    hass.data[ISY994_PROGRAMS] = {}
    for domain in SUPPORTED_DOMAINS:
        hass.data[ISY994_PROGRAMS][domain] = []

    hass.data[ISY994_VARIABLES] = {}
    for domain in SUPPORTED_VARIABLE_DOMAINS:
        hass.data[ISY994_VARIABLES][domain] = []

    isy_config = config.get(DOMAIN)

    user = isy_config.get(CONF_USERNAME)
    password = isy_config.get(CONF_PASSWORD)
    tls_version = isy_config.get(CONF_TLS_VER)
    host = urlparse(isy_config.get(CONF_HOST))
    ignore_identifier = isy_config.get(CONF_IGNORE_STRING)
    sensor_identifier = isy_config.get(CONF_SENSOR_STRING)
    enable_climate = isy_config.get(CONF_ENABLE_CLIMATE)
    isy_variables = isy_config.get(CONF_ISY_VARIABLES)

    if host.scheme == 'http':
        https = False
        port = host.port or 80
    elif host.scheme == 'https':
        https = True
        port = host.port or 443
    else:
        _LOGGER.error("isy994 host value in configuration is invalid")
        return False

    import PyISY
    # Connect to ISY controller.
    isy = PyISY.ISY(host.hostname, port, username=user, password=password,
                    use_https=https, tls_ver=tls_version, log=_LOGGER)
    if not isy.connected:
        return False

    _categorize_nodes(hass, isy.nodes, ignore_identifier, sensor_identifier)
    _categorize_programs(hass, isy.programs)
    _categorize_variables(hass, isy.variables,
                          isy_variables.get(CONF_SENSORS), 'sensor')
    _categorize_variables(hass, isy.variables,
                          isy_variables.get(CONF_BINARY_SENSORS),
                          'binary_sensor')
    _categorize_variables(hass, isy.variables,
                          isy_variables.get(CONF_SWITCHES),
                          'switch')

    if enable_climate and isy.configuration.get('Weather Information'):
        _categorize_weather(hass, isy.climate)

    def stop(event: object) -> None:
        """Stop ISY auto updates."""
        isy.auto_update = False

    # Listen for HA stop to disconnect.
    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, stop)

    # Load platforms for the devices in the ISY controller that we support.
    for component in SUPPORTED_DOMAINS:
        discovery.load_platform(hass, component, DOMAIN, {}, config)

    isy.auto_update = True
    return True


class ISYDevice(Entity):
    """Representation of an ISY994 device."""

    _name = None  # type: str

    def __init__(self, node) -> None:
        """Initialize the insteon device."""
        self._node = node
        self._attrs = {}
        self._change_handler = None
        self._control_handler = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to the node change events."""
        self._change_handler = self._node.status.subscribe(
            'changed', self.on_update)

        if hasattr(self._node, 'controlEvents'):
            self._control_handler = self._node.controlEvents.subscribe(
                self.on_control)

    def on_update(self, event: object) -> None:
        """Handle the update event from the ISY994 Node."""
        self.schedule_update_ha_state()

    def on_control(self, event: object) -> None:
        """Handle a control event from the ISY994 Node."""
        self.hass.bus.fire('isy994_control', {
            'entity_id': self.entity_id,
            'control': event.event,
            'value': event.nval
        })

        # Some attributes are only given by the ISY in the event stream
        # or in a direct query of a node. These are not picked up in PyISY.
        # Translate some common ones here:
        if event.event not in ISY994_EVENT_IGNORE:
            attr_name = ISY994_EVENT_FRIENDLY_NAME.get(event.event,
                                                       event.event)
            self._attrs[attr_name] = int(event.nval)
            self.schedule_update_ha_state()

    @property
    def unique_id(self) -> str:
        """Get the unique identifier of the device."""
        # pylint: disable=protected-access
        if hasattr(self._node, '_id'):
            return self._node._id

        return None

    @property
    def name(self) -> str:
        """Get the name of the device."""
        return self._name or str(self._node.name)

    @property
    def should_poll(self) -> bool:
        """No polling required since we're using the subscription."""
        return False

    @property
    def value(self) -> int:
        """Get the current value of the device."""
        # pylint: disable=protected-access
        return self._node.status._val

    def is_unknown(self) -> bool:
        """Get whether or not the value of this Entity's node is unknown.

        PyISY reports unknown values as -inf
        """
        return self.value == -1 * float('inf')

    @property
    def state(self):
        """Return the state of the ISY device."""
        if self.is_unknown():
            return None
        return super().state

    @property
    def device_state_attributes(self) -> Dict:
        """Get the state attributes for the device.

        The 'aux_properties' in the PyISY Node class are combined with the
        other attributes which have been picked up from the event stream and
        the combined result are returned as the device state attributes.
        """
        attr = {}
        if hasattr(self._node, 'aux_properties'):
            for name, val in self._node.aux_properties.items():
                attr[name] = '{} {}'.format(val.get('value'), val.get('uom'))
        self._attrs.update(attr)
        return attr
        return self._attrs
