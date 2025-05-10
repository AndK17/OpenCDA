# -*- coding: utf-8 -*-
"""
Basic class of CAV
"""
# Author: Runsheng Xu <rxx3386@ucla.edu>
# License: TDG-Attribution-NonCommercial-NoDistrib

import sys
import logging

from opencda.core.actuation.control_manager import ControlManager
from opencda.core.application.platooning.platoon_behavior_agent import PlatooningBehaviorAgent
from opencda.core.common.v2x_manager import V2XManager
from opencda.core.sensing.localization.localization_manager import LocalizationManager
from opencda.core.sensing.perception.perception_manager import PerceptionManager
from opencda.core.safety.safety_manager import SafetyManager
from opencda.core.plan.behavior_agent import BehaviorAgent
from opencda.core.map.map_manager import MapManager
from opencda.core.common.data_dumper import DataDumper

logger = logging.getLogger("cavise.vehicle_manager")


class VehicleManager(object):
    """
    A class manager to embed different modules with vehicle together.

    Parameters
    ----------
    vehicle : carla.Vehicle
        The carla.Vehicle. We need this class to spawn our gnss and imu sensor.

    config_yaml : dict
        The configuration dictionary of this CAV.

    application : list
        The application category, currently support:['single','platoon'].

    carla_map : carla.Map
        The CARLA simulation map.

    cav_world : opencda object
        CAV World. This is used for V2X communication simulation.

    current_time : str
        Timestamp of the simulation beginning, used for data dumping.

    data_dumping : bool
        Indicates whether to dump sensor data during simulation.

    Attributes
    ----------
    v2x_manager : opencda object
        The current V2X manager.

    localizer : opencda object
        The current localization manager.

    perception_manager : opencda object
        The current V2X perception manager.

    agent : opencda object
        The current carla agent that handles the basic behavior
         planning of ego vehicle.

    controller : opencda object
        The current control manager.

    data_dumper : opencda object
        Used for dumping sensor data.
    """

    current_cav_id = 1
    current_platoon_id = 1
    current_unknown_id = 1
    used_ids = set()

    # TODO: application и prefix как будто бы дублируют друг друга, но не факт
    def __init__(
            self,
            vehicle,
            config_yaml,
            application,
            carla_map,
            cav_world,
            current_time='',
            data_dumping=False,
            autogenerate_id_on_failure=True, #TODO: Привязать к конфигу сценария
            prefix='unknown'):

        config_id = config_yaml.get('id')
        self.prefix = prefix if prefix in {'cav', 'platoon'} else 'unknown'

        if config_id is not None:
            try:
                id_int = int(config_id)

                if id_int < 0:
                    raise ValueError("Negative ID")
                candidate = f"{self.prefix}-{id_int}"
                if candidate in VehicleManager.used_ids:
                    logger.warning(f"Duplicate vehicle ID detected: {candidate!r}.")
                    raise ValueError(f"Duplicate vehicle ID detected: {candidate!r}.")
                self.vid = candidate
                VehicleManager.used_ids.add(self.vid)

            except (ValueError, TypeError):
                if autogenerate_id_on_failure:
                    self.vid = self.__generate_unique_vehicle_id()
                    logger.warning(f"Invalid or unavailable vehicle ID in config: {config_id!r}. Assigned auto-generated ID: {self.vid}")
                else:
                    logger.error(f"Invalid or unavailable vehicle ID in config: {config_id!r}.")
                    raise
        else:
            if autogenerate_id_on_failure:
                self.vid = self.__generate_unique_vehicle_id()
                logger.debug(f"No vehicle ID specified in config. Assigned auto-generated ID: {self.vid}")
            else:
                logger.error(f"No vehicle ID specified in config.")
                raise ValueError("No vehicle ID specified in config.")

        self.vehicle = vehicle
        self.carla_map = carla_map

        # retrieve the configure for different modules
        sensing_config = config_yaml['sensing']
        map_config = config_yaml['map_manager']
        behavior_config = config_yaml['behavior']
        control_config = config_yaml['controller']
        v2x_config = config_yaml['v2x']

        # v2x module
        self.v2x_manager = V2XManager(cav_world, v2x_config, self.vid)
        # localization module
        self.localizer = LocalizationManager(vehicle, sensing_config['localization'], carla_map)
        # perception module
        self.perception_manager = PerceptionManager(vehicle=vehicle, 
                                                    config_yaml=sensing_config['perception'], 
                                                    cav_world=cav_world,
                                                    infra_id=self.vid,
                                                    data_dump=data_dumping)
        # map manager
        self.map_manager = MapManager(vehicle, carla_map, map_config)
        # safety manager
        self.safety_manager = SafetyManager(vehicle=vehicle, params=config_yaml['safety_manager'])
        # behavior agent
        self.agent = None
        if 'platoon' in application:
            platoon_config = config_yaml['platoon']
            self.agent = PlatooningBehaviorAgent(vehicle,
                                                 self,
                                                 self.v2x_manager,
                                                 behavior_config,
                                                 platoon_config,
                                                 carla_map)
        else:
            self.agent = BehaviorAgent(vehicle, carla_map, behavior_config)

        # Control module
        self.controller = ControlManager(control_config)

        if data_dumping:
            self.data_dumper = DataDumper(self.perception_manager,
                                          self.vid,
                                          save_time=current_time)
        else:
            self.data_dumper = None

        cav_world.update_vehicle_manager(self)

    def __generate_unique_vehicle_id(self):
        """Generates a unique vehicle ID based on prefix."""
        while True:
            if self.prefix == 'cav':
                candidate = f"cav-{VehicleManager.current_cav_id}"
                VehicleManager.current_cav_id += 1
            elif self.prefix == 'platoon':
                candidate = f"platoon-{VehicleManager.current_platoon_id}"
                VehicleManager.current_platoon_id += 1
            else:
                candidate = f"unknown-{VehicleManager.current_unknown_id}"
                VehicleManager.current_unknown_id += 1

            if candidate not in VehicleManager.used_ids:
                VehicleManager.used_ids.add(candidate)
                return candidate

    def set_destination(
            self,
            start_location,
            end_location,
            clean=False,
            end_reset=True):
        """
        Set global route.

        Parameters
        ----------
        start_location : carla.location
            The CAV start location.

        end_location : carla.location
            The CAV destination.

        clean : bool
             Indicator of whether clean waypoint queue.

        end_reset : bool
            Indicator of whether reset the end location.

        Returns
        -------
        """

        self.agent.set_destination(start_location, end_location, clean, end_reset)

    def update_info(self):
        """
        Call perception and localization module to
        retrieve surrounding info an ego position.
        """
        # localization
        self.localizer.localize()

        ego_pos = self.localizer.get_ego_pos()
        ego_spd = self.localizer.get_ego_spd()

        # object detection
        objects = self.perception_manager.detect(ego_pos)

        # update the ego pose for map manager
        self.map_manager.update_information(ego_pos)

        # this is required by safety manager
        safety_input = {'ego_pos': ego_pos,
                        'ego_speed': ego_spd,
                        'objects': objects,
                        'carla_map': self.carla_map,
                        'world': self.vehicle.get_world(),
                        'static_bev': self.map_manager.static_bev}
        self.safety_manager.update_info(safety_input)

        # leave this for platooning for now
        self.v2x_manager.update_info(ego_pos, ego_spd)
        self.agent.update_information(ego_pos, ego_spd, objects)
        # pass position and speed info to controller
        self.controller.update_info(ego_pos, ego_spd)

    def update_info_v2x(self):
        # TODO: Добавить обновление информации
        pass

    def run_step(self, target_speed=None):
        """
        Execute one step of navigation.
        """
        # visualize the bev map if needed
        self.map_manager.run_step()
        target_speed, target_pos = self.agent.run_step(target_speed)
        control = self.controller.run_step(target_speed, target_pos)

        # dump data
        if self.data_dumper:
            self.data_dumper.run_step(self.perception_manager,
                                      self.localizer,
                                      self.agent)

        return control

    def destroy(self):
        """
        Destroy the actor vehicle
        """
        self.perception_manager.destroy()
        self.localizer.destroy()
        self.vehicle.destroy()
        self.map_manager.destroy()
        self.safety_manager.destroy()
