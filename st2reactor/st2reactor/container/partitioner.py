# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy
import sets
import yaml

from oslo_config import cfg

from st2common import log as logging
from st2common.exceptions.sensors import SensorNotFoundException, \
    SensorPartitionerNotSupportedException, SensorPartitionMapMissingException
from st2common.persistence.keyvalue import KeyValuePair
from st2common.persistence.sensor import SensorType
from st2common.constants.sensors import DEFAULT_PARTITION_LOADER, KVSTORE_PARTITION_LOADER, \
    FILE_PARTITION_LOADER

__all__ = [
    'get_sensors'
]

LOG = logging.getLogger(__name__)


def _get_all_enabled_sensors():
    # only query for enabled sensors.
    sensors = SensorType.query(enabled=True)
    LOG.info('Found %d registered sensors in db scan.', len(sensors))
    return sensors


class DefaultPartitioner(object):

    def __init__(self, sensor_node_name):
        self.sensor_node_name = sensor_node_name

    def get_sensors(self):
        all_enabled_sensors = _get_all_enabled_sensors()

        sensor_refs = self.get_required_sensor_refs()

        # None has special meaning and is different from empty array.
        if sensor_refs is None:
            return all_enabled_sensors

        partition_members = []

        for sensor in all_enabled_sensors:
            sensor_ref = sensor.get_reference()
            if sensor_ref.ref in sensor_refs:
                partition_members.append(sensor)

        return partition_members

    def get_required_sensor_refs(self):
        return None


class KVStorePartitioner(DefaultPartitioner):

    def __init__(self, sensor_node_name):
        super(KVStorePartitioner, self).__init__(sensor_node_name=sensor_node_name)

    def get_required_sensor_refs(self):
        partition_lookup_key = self._get_partition_lookup_key(self.sensor_node_name)

        kvp = KeyValuePair.get_by_name(partition_lookup_key)
        sensor_refs_str = kvp.value if kvp.value else ''
        return sets.Set([sensor_ref.strip() for sensor_ref in sensor_refs_str.split(',')])

    def _get_partition_lookup_key(self, sensor_node_name):
        return '{}.sensor_partition'.format(sensor_node_name)


class FileBasedPartitioner(DefaultPartitioner):

    def __init__(self, sensor_node_name, partition_file):
        super(FileBasedPartitioner, self).__init__(sensor_node_name=sensor_node_name)
        self.partition_file = partition_file

    def get_required_sensor_refs(self):
        with open(self.partition_file, 'r') as f:
            partition_map = yaml.safe_load(f)
            sensor_refs = partition_map.get(self.sensor_node_name, None)
            if sensor_refs is None:
                raise SensorPartitionMapMissingException('Sensor partition not found for %s in %s.',
                                                         self.sensor_node_name, self.partition_file)
            return sets.Set(sensor_refs)


class SingleSensorProvider(object):

    def get_sensors(self, sensor_ref):
        sensor = SensorType.get_by_ref(sensor_ref)
        if not sensor:
            raise SensorNotFoundException('Sensor %s not found in db.' % sensor_ref)
        return [sensor]


PROVIDERS = {
    DEFAULT_PARTITION_LOADER: DefaultPartitioner,
    KVSTORE_PARTITION_LOADER: KVStorePartitioner,
    FILE_PARTITION_LOADER: FileBasedPartitioner
}


def get_sensors():
    if cfg.CONF.sensor_ref:
        return SingleSensorProvider().get_sensors(sensor_ref=cfg.CONF.sensor_ref)
    LOG.info('partition_provider [%s]%s', type(cfg.CONF.sensorcontainer.partition_provider),
             cfg.CONF.sensorcontainer.partition_provider)
    partition_provider_config = copy.copy(cfg.CONF.sensorcontainer.partition_provider)
    partition_provider = partition_provider_config.pop('name')
    sensor_node_name = cfg.CONF.sensorcontainer.sensor_node_name

    provider = PROVIDERS.get(partition_provider.lower(), None)
    if not provider:
        raise SensorPartitionerNotSupportedException(
            'Partition provider %s not found.' % partition_provider)

    # pass in extra config with no analysis
    return provider(sensor_node_name=sensor_node_name, **partition_provider_config).get_sensors()
