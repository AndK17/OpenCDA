# -*- coding: utf-8 -*-
"""
Utilize scenario manager to manage CARLA simulation construction. This script
is used for carla simulation only, and if you want to manage the Co-simulation,
please use cosim_api.py.
"""
# Author: Runsheng Xu <rxx3386@ucla.edu>
# License: TDG-Attribution-NonCommercial-NoDistrib

import math
import random
import logging
import sys
import json

from random import shuffle
from omegaconf import OmegaConf
from omegaconf.listconfig import ListConfig

import carla
import numpy as np

from opencda.core.common.vehicle_manager import VehicleManager
from opencda.core.application.platooning.platooning_manager import PlatooningManager
from opencda.core.common.rsu_manager import RSUManager
from opencda.core.common.cav_world import CavWorld
from opencda.scenario_testing.utils.customized_map_api import load_customized_world, bcolors

logger = logging.getLogger("cavise.sim_api")


def car_blueprint_filter(blueprint_library, carla_version="0.9.15"):
    """
    Exclude the uncommon vehicles from the default CARLA blueprint library
    (i.e., isetta, carlacola, cybertruck, t2).

    Parameters
    ----------
    blueprint_library : carla.blueprint_library
        The blueprint library that contains all models.

    carla_version : str
        CARLA simulator version, currently support 0.9.11 and 0.9.12. We need
        this as since CARLA 0.9.12 the blueprint name has been changed a lot.

    Returns
    -------
    blueprints : list
        The list of suitable blueprints for vehicles.
    """

    if carla_version == "0.9.15" or carla_version == "0.9.14":
        logger.info(f"Carla {carla_version} version is selected")
        blueprints = [
            blueprint_library.find("vehicle.audi.a2"),
            blueprint_library.find("vehicle.audi.tt"),
            blueprint_library.find("vehicle.ford.ambulance"),
            blueprint_library.find("vehicle.ford.crown"),
            blueprint_library.find("vehicle.mini.cooper_s_2021"),
            blueprint_library.find("vehicle.nissan.micra"),
            blueprint_library.find("vehicle.nissan.patrol"),
            blueprint_library.find("vehicle.nissan.patrol_2021"),
            blueprint_library.find("vehicle.tesla.cybertruck"),
            blueprint_library.find("vehicle.volkswagen.t2"),
            blueprint_library.find("vehicle.volkswagen.t2_2021"),
            blueprint_library.find("vehicle.micro.microlino"),
            blueprint_library.find("vehicle.dodge.charger_police"),
            blueprint_library.find("vehicle.dodge.charger_police_2020"),
            blueprint_library.find("vehicle.dodge.charger_2020"),
            blueprint_library.find("vehicle.lincoln.mkz_2020"),
            blueprint_library.find("vehicle.seat.leon"),
            blueprint_library.find("vehicle.nissan.patrol"),
            blueprint_library.find("vehicle.nissan.micra"),
        ]
    else:
        sys.exit(
            "Since v0.1.4, we do not support version earlier than "
            "CARLA v0.9.14. If you want to use early CARLA version including"
            "0.9.11 and 0.9.12, please use OpenCDA v0.1.3."
        )

    return blueprints


def multi_class_vehicle_blueprint_filter(label, blueprint_library, bp_meta):
    """
    Get a list of blueprints that have the class equals the specified label.

    Parameters
    ----------
    label : str
        Specified blueprint.

    blueprint_library : carla.blueprint_library
        The blueprint library that contains all models.

    bp_meta : dict
        Dictionary of {blueprint name: blueprint class}.

    Returns
    -------
    blueprints : list
        List of blueprints that have the class equals the specified label.

    """
    blueprints = [blueprint_library.find(k) for k, v in bp_meta.items() if v["class"] == label]
    return blueprints


class ScenarioManager:
    """
    The manager that controls simulation construction, backgound traffic
    generation and CAVs spawning.

    Parameters
    ----------
    scenario_params : dict
        The dictionary contains all simulation configurations.

    carla_version : str
        CARLA simulator version, it currently supports 0.9.11 and 0.9.12

    xodr_path : str
        The xodr file to the customized map, default: None.

    town : str
        Town name if not using customized map, eg. 'Town06'.

    apply_ml : bool
        Whether need to load dl/ml model(pytorch required) in this simulation.

    Attributes
    ----------
    client : carla.client
        The client that connects to carla server.

    world : carla.world
        Carla simulation server.

    origin_settings : dict
        The origin setting of the simulation server.

    cav_world : opencda object
        CAV World that contains the information of all CAVs.

    carla_map : carla.map
        Car;a HD Map.

    """

    def __init__(self, scenario_params, apply_ml, carla_version, xodr_path=None, town=None, cav_world=None):
        self.scenario_params = scenario_params
        self.carla_version = carla_version
        self.world = None

        simulation_config = scenario_params["world"]

        # set random seed if stated
        if "seed" in simulation_config:
            np.random.seed(simulation_config["seed"])
            random.seed(simulation_config["seed"])

        self.client = carla.Client("carla", simulation_config["client_port"])
        self.client.set_timeout(10.0)

        if xodr_path:
            self.world = load_customized_world(xodr_path, self.client)
        elif town:
            try:
                self.world = self.client.load_world(town)
            except RuntimeError as error:
                logger.error(
                    f"{bcolors.FAIL}{town} probably is not in your CARLA repo! Please download all town maps to your CARLA repo!{bcolors.ENDC}"
                )
                logger.error(error)
        else:
            self.world = self.client.get_world()

        if not self.world:
            sys.exit("- World loading failed")

        self.origin_settings = self.world.get_settings()
        new_settings = self.world.get_settings()

        if simulation_config["sync_mode"]:
            new_settings.synchronous_mode = True
            new_settings.fixed_delta_seconds = simulation_config["fixed_delta_seconds"]
        else:
            sys.exit("ERROR: Current version only supports sync simulation mode")

        self.world.apply_settings(new_settings)

        # set weather
        weather = self.set_weather(simulation_config["weather"])
        self.world.set_weather(weather)

        # Define probabilities for each type of blueprint
        self.use_multi_class_bp = scenario_params["blueprint"]["use_multi_class_bp"] if "blueprint" in scenario_params else False
        if self.use_multi_class_bp:
            # bbx/blueprint meta
            with open(scenario_params["blueprint"]["bp_meta_path"]) as f:
                self.bp_meta = json.load(f)
            self.bp_class_sample_prob = scenario_params["blueprint"]["bp_class_sample_prob"]

            # normalize probability
            self.bp_class_sample_prob = {k: v / sum(self.bp_class_sample_prob.values()) for k, v in self.bp_class_sample_prob.items()}

        self.cav_world = cav_world
        self.carla_map = self.world.get_map()
        self.apply_ml = apply_ml

    @staticmethod
    def set_weather(weather_settings):
        """
        Set CARLA weather params.

        Parameters
        ----------
        weather_settings : dict
            The dictionary that contains all parameters of weather.

        Returns
        -------
        The CARLA weather setting.
        """
        weather = carla.WeatherParameters(
            sun_altitude_angle=weather_settings["sun_altitude_angle"],
            cloudiness=weather_settings["cloudiness"],
            precipitation=weather_settings["precipitation"],
            precipitation_deposits=weather_settings["precipitation_deposits"],
            wind_intensity=weather_settings["wind_intensity"],
            fog_density=weather_settings["fog_density"],
            fog_distance=weather_settings["fog_distance"],
            fog_falloff=weather_settings["fog_falloff"],
            wetness=weather_settings["wetness"],
        )
        return weather

    def spawn_custom_actor(self, spawn_transform, config, fallback_model):
        model = config.get("model", fallback_model)
        cav_vehicle_bp = self.world.get_blueprint_library().find(model)

        color = config.get("color")
        if color is not None:
            try:
                cav_vehicle_bp.set_attribute("color", ",".join(map(str, color)))
            except IndexError:
                logger.warning(f"Vehicle model {cav_vehicle_bp.id} does not support the 'color' attribute. Skipping.")

        return self.world.spawn_actor(cav_vehicle_bp, spawn_transform)

    def create_vehicle_manager(self, application, map_helper=None, data_dump=False, fallback_model: str = "vehicle.lincoln.mkz_2017"):
        """
        Create a list of single CAVs.

        Parameters
        ----------
        application : list
            The application purpose, a list, eg. ['single'], ['platoon'].

        map_helper : function
            A function to help spawn vehicle on a specific position in
            a specific map.

        data_dump : bool
            Whether to dump sensor data.

        fallback_model: str
            Fallback cav model if none provided by config.

        Returns
        -------
        single_cav_list : list
            A list contains all single CAVs' vehicle manager.
        """
        single_cav_list = []
        cav_carla_list = {}

        if self.scenario_params.get("scenario") is None or self.scenario_params["scenario"].get("single_cav_list", None) is None:
            logger.info("No CAV was created")
            return single_cav_list, cav_carla_list

        for i, cav_config in enumerate(self.scenario_params["scenario"]["single_cav_list"]):
            # in case the cav wants to join a platoon later
            # it will be empty dictionary for single cav application
            platoon_base = OmegaConf.create({"platoon": self.scenario_params.get("platoon_base", {})})
            cav_config = OmegaConf.merge(self.scenario_params["vehicle_base"], platoon_base, cav_config)
            # if the spawn position is a single scalar, we need to use map
            # helper to transfer to spawn transform
            if "spawn_special" not in cav_config:
                spawn_transform = carla.Transform(
                    carla.Location(x=cav_config["spawn_position"][0], y=cav_config["spawn_position"][1], z=cav_config["spawn_position"][2]),
                    carla.Rotation(pitch=cav_config["spawn_position"][5], yaw=cav_config["spawn_position"][4], roll=cav_config["spawn_position"][3]),
                )
            else:
                spawn_transform = map_helper(self.carla_version, *cav_config["spawn_special"])

            vehicle = self.spawn_custom_actor(spawn_transform, cav_config, fallback_model)

            # create vehicle manager for each cav
            vehicle_manager = VehicleManager(
                vehicle,
                cav_config,
                application,
                self.carla_map,
                self.cav_world,
                current_time=self.scenario_params["current_time"],
                data_dumping=data_dump,
                prefix="cav",
            )

            cav_carla_list[vehicle.id] = vehicle_manager.vid

            self.world.tick()

            vehicle_manager.v2x_manager.set_platoon(None)

            destination = carla.Location(x=cav_config["destination"][0], y=cav_config["destination"][1], z=cav_config["destination"][2])
            vehicle_manager.update_info()
            vehicle_manager.set_destination(vehicle_manager.vehicle.get_location(), destination, clean=True)

            single_cav_list.append(vehicle_manager)
            logger.info(f"Created CAV with id {vehicle_manager.vid}")

        return single_cav_list, cav_carla_list

    def create_platoon_manager(self, map_helper=None, data_dump: bool = False, fallback_model: str = "vehicle.lincoln.mkz_2017"):
        """
        Create a list of platoons.

        Parameters
        ----------
        map_helper : function
            A function to help spawn vehicle on a specific position in a
            specific map.

        data_dump : bool
            Whether to dump sensor data.

        fallback_model: str
            Fallback cav model if none provided by config.

        Returns
        -------
        single_cav_list : list
            A list contains all single CAVs' vehicle manager.
        """
        platoon_list = []
        platoon_carla_ids = {}

        self.cav_world = CavWorld(self.apply_ml)

        if self.scenario_params.get("scenario") is None or self.scenario_params["scenario"].get("platoon_list", None) is None:
            logger.info("No platoon was created")
            return platoon_list, platoon_carla_ids

        # create platoons
        for i, platoon in enumerate(self.scenario_params["scenario"]["platoon_list"]):
            platoon = OmegaConf.merge(self.scenario_params["platoon_base"], platoon)
            platoon_manager = PlatooningManager(platoon, self.cav_world)

            for j, cav_config in enumerate(platoon["members"]):
                platoon_base = OmegaConf.create({"platoon": platoon})
                cav_config = OmegaConf.merge(self.scenario_params["vehicle_base"], platoon_base, cav_config)
                if "spawn_special" not in cav_config:
                    spawn_transform = carla.Transform(
                        carla.Location(x=cav_config["spawn_position"][0], y=cav_config["spawn_position"][1], z=cav_config["spawn_position"][2]),
                        carla.Rotation(
                            pitch=cav_config["spawn_position"][5], yaw=cav_config["spawn_position"][4], roll=cav_config["spawn_position"][3]
                        ),
                    )
                else:
                    spawn_transform = map_helper(self.carla_version, *cav_config["spawn_special"])

                vehicle = self.spawn_custom_actor(spawn_transform, cav_config, fallback_model)

                # create vehicle manager for each cav
                vehicle_manager = VehicleManager(
                    vehicle,
                    cav_config,
                    ["platoon"],
                    self.carla_map,
                    self.cav_world,
                    current_time=self.scenario_params["current_time"],
                    data_dumping=data_dump,
                    prefix="platoon",
                )

                platoon_carla_ids[vehicle.id] = vehicle_manager.vid

                # add the vehicle manager to platoon
                if j == 0:
                    platoon_manager.set_lead(vehicle_manager)
                else:
                    platoon_manager.add_member(vehicle_manager, leader=False)

            self.world.tick()
            destination = carla.Location(x=platoon["destination"][0], y=platoon["destination"][1], z=platoon["destination"][2])

            platoon_manager.set_destination(destination)
            platoon_manager.update_member_order()
            platoon_list.append(platoon_manager)

        return platoon_list, platoon_carla_ids

    def create_rsu_manager(self, data_dump):
        """
        Create a list of RSU.

        Parameters
        ----------
        data_dump : bool
            Whether to dump sensor data.

        Returns
        -------
        rsu_list : list
            A list contains all rsu managers..
        """
        rsu_list = []
        rsu_carla_ids = {}

        if self.scenario_params.get("scenario") is None or self.scenario_params["scenario"].get("rsu_list", None) is None:
            logger.info("No RSU was created")
            return rsu_list, rsu_carla_ids

        for rsu_config in self.scenario_params["scenario"]["rsu_list"]:
            rsu_config = OmegaConf.merge(self.scenario_params["rsu_base"], rsu_config)
            default_model = "static.prop.gnome"
            static_bp = self.world.get_blueprint_library().find(default_model)

            spawn_transform = carla.Transform(
                carla.Location(x=rsu_config["spawn_position"][0], y=rsu_config["spawn_position"][1], z=rsu_config["spawn_position"][2]),
                carla.Rotation(pitch=rsu_config["spawn_position"][5], yaw=rsu_config["spawn_position"][4], roll=rsu_config["spawn_position"][3]),
            )

            actor = self.world.spawn_actor(static_bp, spawn_transform)

            rsu_manager = RSUManager(self.world, rsu_config, self.carla_map, self.cav_world, self.scenario_params["current_time"], data_dump)

            rsu_carla_ids[actor.id] = rsu_manager.rid

            rsu_list.append(rsu_manager)
            logger.info(f"Created RSU with id {rsu_manager.rid}")

        return rsu_list, rsu_carla_ids

    def spawn_vehicles_by_list(self, tm, traffic_config, bg_list):
        """
        Spawn the traffic vehicles by the given list.

        Parameters
        ----------
        tm : carla.TrafficManager
            Traffic manager.

        traffic_config : dict
            Background traffic configuration.

        bg_list : list
            The list contains all background traffic.

        Returns
        -------
        bg_list : list
            Update traffic list.
        """

        blueprint_library = self.world.get_blueprint_library()
        if not self.use_multi_class_bp:
            ego_vehicle_random_list = car_blueprint_filter(blueprint_library, self.carla_version)
        else:
            label_list = list(self.bp_class_sample_prob.keys())
            prob = [self.bp_class_sample_prob[itm] for itm in label_list]

        # if not random select, we always choose lincoln.mkz with green color
        color = "0, 255, 0"
        default_model = "vehicle.lincoln.mkz_2020"
        ego_vehicle_bp = blueprint_library.find(default_model)

        for i, vehicle_config in enumerate(traffic_config["vehicle_list"]):
            spawn_transform = carla.Transform(
                carla.Location(x=vehicle_config["spawn_position"][0], y=vehicle_config["spawn_position"][1], z=vehicle_config["spawn_position"][2]),
                carla.Rotation(
                    pitch=vehicle_config["spawn_position"][5], yaw=vehicle_config["spawn_position"][4], roll=vehicle_config["spawn_position"][3]
                ),
            )

            if not traffic_config["random"]:
                ego_vehicle_bp.set_attribute("color", color)

            else:
                # sample a bp from various classes
                if self.use_multi_class_bp:
                    label = np.random.choice(label_list, p=prob)
                    # Given the label (class), find all associated blueprints in CARLA
                    ego_vehicle_random_list = multi_class_vehicle_blueprint_filter(label, blueprint_library, self.bp_meta)
                ego_vehicle_bp = random.choice(ego_vehicle_random_list)

                if ego_vehicle_bp.has_attribute("color"):
                    color = random.choice(ego_vehicle_bp.get_attribute("color").recommended_values)
                    ego_vehicle_bp.set_attribute("color", color)

            vehicle = self.world.spawn_actor(ego_vehicle_bp, spawn_transform)
            vehicle.set_autopilot(True, 8000)

            if "vehicle_speed_perc" in vehicle_config:
                tm.vehicle_percentage_speed_difference(vehicle, vehicle_config["vehicle_speed_perc"])
            tm.auto_lane_change(vehicle, traffic_config["auto_lane_change"])

            bg_list.append(vehicle)

        return bg_list

    def spawn_vehicle_by_range(self, tm, traffic_config, bg_list):
        """
        Spawn the traffic vehicles by the given range.

        Parameters
        ----------
        tm : carla.TrafficManager
            Traffic manager.

        traffic_config : dict
            Background traffic configuration.

        bg_list : list
            The list contains all background traffic.

        Returns
        -------
        bg_list : list
            Update traffic list.
        """
        blueprint_library = self.world.get_blueprint_library()
        if not self.use_multi_class_bp:
            ego_vehicle_random_list = car_blueprint_filter(blueprint_library, self.carla_version)
        else:
            label_list = list(self.bp_class_sample_prob.keys())
            prob = [self.bp_class_sample_prob[itm] for itm in label_list]

        # if not random select, we always choose lincoln.mkz with green color
        color = "0, 255, 0"
        default_model = "vehicle.lincoln.mkz_2020"
        ego_vehicle_bp = blueprint_library.find(default_model)

        spawn_ranges = traffic_config["range"]
        spawn_set = set()
        spawn_num = 0

        for spawn_range in spawn_ranges:
            spawn_num += spawn_range[6]
            x_min, x_max, y_min, y_max = math.floor(spawn_range[0]), math.ceil(spawn_range[1]), math.floor(spawn_range[2]), math.ceil(spawn_range[3])

            for x in range(x_min, x_max, int(spawn_range[4])):
                for y in range(y_min, y_max, int(spawn_range[5])):
                    location = carla.Location(x=x, y=y, z=0.3)
                    way_point = self.carla_map.get_waypoint(location).transform

                    spawn_set.add(
                        (
                            way_point.location.x,
                            way_point.location.y,
                            way_point.location.z,
                            way_point.rotation.roll,
                            way_point.rotation.yaw,
                            way_point.rotation.pitch,
                        )
                    )
        count = 0
        spawn_list = list(spawn_set)
        shuffle(spawn_list)

        while count < spawn_num:
            if len(spawn_list) == 0:
                break

            coordinates = spawn_list[0]
            spawn_list.pop(0)

            spawn_transform = carla.Transform(
                carla.Location(x=coordinates[0], y=coordinates[1], z=coordinates[2] + 0.3),
                carla.Rotation(roll=coordinates[3], yaw=coordinates[4], pitch=coordinates[5]),
            )
            if not traffic_config["random"]:
                ego_vehicle_bp.set_attribute("color", color)

            else:
                # sample a bp from various classes
                if self.use_multi_class_bp:
                    label = np.random.choice(label_list, p=prob)
                    # Given the label (class), find all associated blueprints in CARLA
                    ego_vehicle_random_list = multi_class_vehicle_blueprint_filter(label, blueprint_library, self.bp_meta)
                    if not ego_vehicle_random_list:
                        logger.warning(f"No blueprints found for label: {label}, using default blueprint")
                        ego_vehicle_random_list = blueprint_library.filter("vehicle.*")
                ego_vehicle_bp = random.choice(ego_vehicle_random_list)
                if ego_vehicle_bp.has_attribute("color"):
                    color = random.choice(ego_vehicle_bp.get_attribute("color").recommended_values)
                    ego_vehicle_bp.set_attribute("color", color)

            vehicle = self.world.try_spawn_actor(ego_vehicle_bp, spawn_transform)

            if not vehicle:
                continue

            vehicle.set_autopilot(True, 8000)
            tm.auto_lane_change(vehicle, traffic_config["auto_lane_change"])

            tm.ignore_lights_percentage(vehicle, traffic_config["ignore_lights_percentage"])
            tm.ignore_signs_percentage(vehicle, traffic_config["ignore_signs_percentage"])
            tm.ignore_vehicles_percentage(vehicle, traffic_config["ignore_vehicles_percentage"])
            tm.ignore_walkers_percentage(vehicle, traffic_config["ignore_walkers_percentage"])
            # left/right lane change
            if traffic_config["random_left_lanechange_percentage"] != 0:
                tm.random_left_lanechange_percentage(vehicle, traffic_config["random_left_lanechange_percentage"])
            if traffic_config["random_right_lanechange_percentage"] != 0:
                tm.random_right_lanechange_percentage(vehicle, traffic_config["random_right_lanechange_percentage"])
            # each vehicle have slight different speed
            tm.vehicle_percentage_speed_difference(vehicle, traffic_config["global_speed_perc"] + random.randint(-30, 30))

            bg_list.append(vehicle)
            count += 1

        return bg_list

    def create_traffic_carla(self):
        """
        Create traffic flow.

        Returns
        -------
        tm : carla.traffic_manager
            Carla traffic manager.

        bg_list : list
            The list that contains all the background traffic vehicles.
        """
        bg_list = []

        if self.scenario_params.get("carla_traffic_manager") is None:
            logger.info("No Carla traffic flow was created")
            return None, bg_list

        traffic_config = self.scenario_params["carla_traffic_manager"]
        tm = self.client.get_trafficmanager()

        tm.set_global_distance_to_leading_vehicle(traffic_config["global_distance"])
        tm.set_synchronous_mode(traffic_config["sync_mode"])
        tm.set_osm_mode(traffic_config["set_osm_mode"])
        tm.global_percentage_speed_difference(traffic_config["global_speed_perc"])

        if isinstance(traffic_config["vehicle_list"], list) or isinstance(traffic_config["vehicle_list"], ListConfig):
            bg_list = self.spawn_vehicles_by_list(tm, traffic_config, bg_list)
        else:
            bg_list = self.spawn_vehicle_by_range(tm, traffic_config, bg_list)

        if not bg_list:
            logger.info("No Carla traffic flow was created")
        else:
            logger.info("CARLA traffic flow generated")
        return tm, bg_list

    def tick(self):
        """
        Tick the server.
        """
        self.world.tick()

    def destroyActors(self):
        """
        Destroy all actors in the world.
        """

        actor_list = self.world.get_actors()
        for actor in actor_list:
            actor.destroy()

    def close(self):
        """
        Simulation close.
        """
        # restore to origin setting
        self.world.apply_settings(self.origin_settings)
