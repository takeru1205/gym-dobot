import numpy as np
from gym_dobot.envs import rotations, robot_env, utils
from mujoco_py.generated import const


def goal_distance(goal_a, goal_b):
    assert goal_a.shape == goal_b.shape
    return np.linalg.norm(goal_a - goal_b, axis=-1)


class DobotEnv(robot_env.RobotEnv):
    """Superclass for all Dobot environments.
    """

    def __init__(
        self, model_path, n_substeps, gripper_extra_height, block_gripper,
        has_object, target_in_the_air, target_offset, obj_range, target_range,
        distance_threshold, initial_qpos, reward_type,rand_dom,
    ):
        """Initializes a new Dobot environment.

        Args:
            model_path (string): path to the environments XML file
            n_substeps (int): number of substeps the simulation runs on every call to step
            gripper_extra_height (float): additional height above the table when positioning the gripper
            block_gripper (boolean): whether or not the gripper is blocked (i.e. not movable) or not
            has_object (boolean): whether or not the environment has an object
            target_in_the_air (boolean): whether or not the target should be in the air above the table or on the table surface
            target_offset (float or array with 3 elements): offset of the target
            obj_range (float): range of a uniform distribution for sampling initial object positions
            target_range (float): range of a uniform distribution for sampling a target
            distance_threshold (float): the threshold after which a goal is considered achieved
            initial_qpos (dict): a dictionary of joint names and values that define the initial configuration
            reward_type ('sparse' or 'dense'): the reward type, i.e. sparse or dense
            rand_dom ('False' or 'True'): Whether to use domain randomization
        """
        self.gripper_extra_height = gripper_extra_height
        self.block_gripper = block_gripper
        self.has_object = has_object
        self.target_in_the_air = target_in_the_air
        self.target_offset = target_offset
        self.obj_range = obj_range
        self.target_range = target_range
        self.distance_threshold = distance_threshold
        self.reward_type = reward_type
        self.rand_dom = rand_dom

        super(DobotEnv, self).__init__(
            model_path=model_path, n_substeps=n_substeps, n_actions=4,
            initial_qpos=initial_qpos)

    # GoalEnv methods
    # ----------------------------

    def compute_reward(self, achieved_goal, goal, info):
        # Compute distance between goal and the achieved goal.
        d = goal_distance(achieved_goal, goal)
        if self.reward_type == 'sparse':
            return -(d > self.distance_threshold).astype(np.float32)
        else:
            return -d

    # RobotEnv methods
    # ----------------------------

    def _step_callback(self):
        if self.block_gripper:
            self.sim.data.set_joint_qpos('dobot:l_gripper_joint', 0.)
            self.sim.data.set_joint_qpos('dobot:r_gripper_joint', 0.)
            self.sim.forward()

    def _set_action(self, action):
        assert action.shape == (4,)
        action = action.copy()  # ensure that we don't change the action outside of this scope
        pos_ctrl, gripper_ctrl = action[:3], action[3]
        #pos_ctrl_low = [-0.05,-0.05,-0.05]
        #pos_ctrl_high = [0.05,0.05,0.05]
        #pos_ctrl = np.clip(pos_ctrl,pos_ctrl_low,pos_ctrl_high)
        pos_ctrl *= 0.05 # limit maximum change in position
        rot_ctrl = [-1, 0, 0, 0]  # fixed rotation of the end effector, expressed as a quaternion
        gripper_ctrl = np.array([gripper_ctrl, -gripper_ctrl])
        assert gripper_ctrl.shape == (2,)
        if self.block_gripper:
            gripper_ctrl = np.zeros_like(gripper_ctrl)
        action = np.concatenate([pos_ctrl, rot_ctrl, gripper_ctrl])

        # Apply action to simulation.
        utils.ctrl_set_action(self.sim, action)
        utils.mocap_set_action(self.sim, action)

    def _get_obs(self):
        # positions
        grip_pos = self.sim.data.get_site_xpos('dobot:grip')
        dt = self.sim.nsubsteps * self.sim.model.opt.timestep
        grip_velp = self.sim.data.get_site_xvelp('dobot:grip') * dt
        robot_qpos, robot_qvel = utils.robot_get_obs(self.sim)
        if self.has_object:
            object_pos = self.sim.data.get_site_xpos('object0')
            # rotations
            object_rot = rotations.mat2euler(self.sim.data.get_site_xmat('object0'))
            # velocities
            object_velp = self.sim.data.get_site_xvelp('object0') * dt
            object_velr = self.sim.data.get_site_xvelr('object0') * dt
            # gripper state
            object_rel_pos = object_pos - grip_pos
            object_velp -= grip_velp
        else:
            object_pos = object_rot = object_velp = object_velr = object_rel_pos = np.zeros(0)
        gripper_state = robot_qpos[-2:]
        gripper_vel = robot_qvel[-2:] * dt  # change to a scalar if the gripper is made symmetric

        if not self.has_object:
            achieved_goal = grip_pos.copy()
        else:
            achieved_goal = np.squeeze(object_pos.copy())
        obs = np.concatenate([
            grip_pos, object_pos.ravel(), object_rel_pos.ravel(), gripper_state, object_rot.ravel(),
            object_velp.ravel(), object_velr.ravel(), grip_velp, gripper_vel,
        ])

        return {
            'observation': obs.copy(),
            'achieved_goal': achieved_goal.copy(),
            'desired_goal': self.goal.copy(),
        }

    def _viewer_setup(self):
        body_id = self.sim.model.body_name2id('dobot:gripper_link')
        lookat = self.sim.data.body_xpos[body_id]
        for idx, value in enumerate(lookat):
            self.viewer.cam.lookat[idx] = value
        #print(self.viewer.__dict__)

        #print(self.viewer.sim.render())
        self.viewer.cam.distance = 2.2
        self.viewer.cam.azimuth = 145.
        self.viewer.cam.elevation = -25.

        # self.viewer.cam.fixedcamid = 0
        # self.viewer.cam.type = 2
        self.viewer._hide_overlay = True

    def _render_callback(self):
        # Visualize target.
        sites_offset = (self.sim.data.site_xpos - self.sim.model.site_pos).copy()
        site_id = self.sim.model.site_name2id('target0')
        self.sim.model.site_pos[site_id] = self.goal - sites_offset[0]
        self.sim.forward()

    def _reset_sim(self):
        self.sim.set_state(self.initial_state)
        if self.viewer!= None and self.rand_dom: 
            for name in self.sim.model.geom_names:
                self.modder.rand_all(name)

            #Camera
            pos = np.array([0,-1,1]) + self.np_random.uniform(-0.1,0.1,size=3)
            self.cam_modder.set_pos('camera0',pos)

            #Light
            self.light_modder.set_castshadow('light0',1)
            pos = np.array([0.8,0.9,3])
            pos[:2] = pos[:2] + self.np_random.uniform(-0.85,0.85,size=2)
            self.light_modder.set_pos('light0',pos)
        

        # Randomize start position of object.
        # if self.has_object:
        #     object_xpos = self.initial_gripper_xpos[:2]
        #     #while np.linalg.norm(object_xpos - self.initial_gripper_xpos[:2]) < 0.01:
        #     object_xpos = self.initial_gripper_xpos[:2] + self.np_random.uniform(-self.obj_range, self.obj_range, size=2)
        #     #object_xpos[:2] = np.clip(object_xpos,[0.6,0.55],[1.0,0.95,0.47])
        #     object_qpos = self.sim.data.get_joint_qpos('object0:joint')
        #     assert object_qpos.shape == (7,)
        #     object_qpos[:2] = object_xpos
        #     self.sim.data.set_joint_qpos('object0:joint', object_qpos)

        if self.has_object:
            pos = np.array([0.8,0.685,0.22725])
            size = np.array([0.335,0.165,0.21225]) - 0.025
            up = pos + size
            low = pos - size
            object_xpos = np.array([self.np_random.uniform(low[0],up[0]),self.np_random.uniform(low[1],up[1])])
            object_qpos = self.sim.data.get_joint_qpos('object0:joint')
            assert object_qpos.shape == (7,)
            object_qpos[:2] = object_xpos
            object_qpos[2] = 0.032
            self.sim.data.set_joint_qpos('object0:joint', object_qpos)
            

        self.sim.forward()
        return True

    def _sample_goal(self):
        # if self.has_object:
        #     goal = self.initial_gripper_xpos[:3] + self.np_random.uniform(-self.target_range, self.target_range, size=3)
        #     goal += self.target_offset
        #     goal[2] = self.height_offset
        #     if self.target_in_the_air and self.np_random.uniform() < 0.5:
        #         goal[2] += self.np_random.uniform(0, 0.25)
        # else:
        #     goal = self.initial_gripper_xpos[:3] + self.np_random.uniform(-0.15, 0.15, size=3)
        # return goal.copy()

        pos = np.array([0.8,0.685,0.22725])
        size = np.array([0.335,0.165,0.21225]) - 0.025
        up = pos + size
        low = pos - size
        goal = np.array([self.np_random.uniform(low[0],up[0]),self.np_random.uniform(low[1],up[1]),0.032])

        if self.has_object:
            if self.target_in_the_air and self.np_random.uniform() < 0.5:
                goal[2] += self.np_random.uniform(0, 0.25)
        else:
            goal[2] += self.np_random.uniform(0, 0.25)

        return goal.copy()
        
    def _is_success(self, achieved_goal, desired_goal):
        d = goal_distance(achieved_goal, desired_goal)
        return (d < self.distance_threshold).astype(np.float32)

    def _env_setup(self, initial_qpos):
        for name, value in initial_qpos.items():
            self.sim.data.set_joint_qpos(name, value)
        utils.reset_mocap_welds(self.sim)
        self.sim.forward()

        # Move end effector into position.
        #gripper_target = np.array([0.001, -1.4, -0.431 + self.gripper_extra_height]) + self.sim.data.get_site_xpos('dobot:grip')
        gripper_target = np.array([0.8,0.76,0.37])
        gripper_rotation = np.array([-1, 0., 0., 0])
        self.sim.data.set_mocap_pos('dobot:mocap', gripper_target)
        self.sim.data.set_mocap_quat('dobot:mocap', gripper_rotation)
        for _ in range(10):
            self.sim.step()

        # Extract information for sampling goals.
        #self.initial_gripper_xpos = self.sim.data.get_site_xpos('dobot:grip').copy()
        self.initial_gripper_xpos = np.array([0.8, 0.685, 0.2975])
        #print(self.initial_gripper_xpos)
        if self.has_object:
            self.height_offset = self.sim.data.get_site_xpos('object0')[2]

    def capture(self):
        if self.viewer == None:
            pass
        else:
            self.viewer.cam.fixedcamid = 0
            self.viewer.cam.type = const.CAMERA_FIXED
            img = self.viewer._read_pixels_as_in_window()
            return img