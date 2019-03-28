"""
Wrappers for VGDL Games
"""
import os
import random
import numpy as np

import gym
import gym_gvgai

from game.level import Level

import a2c_ppo_acktr.envs as torch_env

from baselines import bench
from baselines.common.vec_env.dummy_vec_env import DummyVecEnv
from baselines.common.vec_env.shmem_vec_env import ShmemVecEnv
from baselines.common.vec_env.vec_normalize import VecNormalize

def make_env(env_def, generator, seed, rank, log_dir, allow_early_resets):
    def _thunk():
        if(generator):
            env = GridGame(env_def.name, env_def.length, env_def.state_shape, env_def.ascii, generator)
        else:
            env = GridGame(env_def.name, env_def.length, env_def.state_shape)

        env.seed(seed + rank)
        obs_shape = env.observation_space.shape

        if log_dir is not None:
            env = bench.Monitor(
                env,
                os.path.join(log_dir, str(rank)),
                allow_early_resets=allow_early_resets)
        return env
    return _thunk

def make_vec_envs(env_def, generator, seed, num_processes, gamma, log_dir, device, allow_early_resets, num_frame_stack=None):

    envs = [make_env(env_def, generator, seed, i, log_dir, allow_early_resets) for i in range(num_processes)]

    if len(envs) > 1:
        envs = ShmemVecEnv(envs, context='fork')
    else:
        envs = DummyVecEnv(envs)

    if len(envs.observation_space.shape) == 1:
        if gamma is None:
            envs = VecNormalize(envs, ret=False)
        else:
            envs = VecNormalize(envs, gamma=gamma)

    envs = torch_env.VecPyTorch(envs, device)

    if num_frame_stack is not None:
        envs = torch_env.VecPyTorchFrameStack(envs, num_frame_stack, device)
    #elif len(envs.observation_space.shape) == 3:
    #    envs = torch_env.VecPyTorchFrameStack(envs, 4, device)

    return envs

#Look at baseline wrappers and make a wrapper file: New vec_wrapper + game_wrapper
class GridGame(gym.Wrapper):
    def __init__(self, game, play_length, shape, ascii_map=None, generator=None):
        """Returns Grid instead of pixels
        Sets the reward
        Generates new level on reset
        #PPO wants to maximize, Generator wants a score of 0
        --------
        """
        self.name = game
        self.env = gym_gvgai.make('gvgai-{}-lvl0-v1'.format(game))
        self.levels = Level(generator, ascii_map) if generator!=None else None
        gym.Wrapper.__init__(self, self.env)

        self.compiles = True
        self.state = None
        self.steps = 0
        self.play_length = play_length
        self.shape = shape
        self.observation_space = gym.spaces.Box(low=0, high=1, shape=shape, dtype=np.float)

    def reset(self):
        self.steps = 0
        state = self.set_level()
        return state

    def step(self, action):
        if(not self.compiles):
           return self.state, -10, True, {}
        _, _, done, info = self.env.step(action)
        if(self.steps >= self.play_length):
            done = True
        reward = self.get_reward(done, info["winner"] )
        state = self.get_state(info['grid'])
        self.steps += 1
        return state, reward, done, {}

    # def get_reward(self, isOver, winner):
    #     if(isOver):
    #         if(winner == 'PLAYER_WINS'):
    #             return 1
    #         else:
    #             return -1
    #     else:
    #         return -1/self.play_length

    def get_reward(self, isOver, winner):
        if(isOver):
            if(winner == 'PLAYER_WINS'):
                return  1.1 - self.steps/self.play_length
            else:
                return -1.1 + self.steps/self.play_length
        else:
            return 0

    def get_state(self, grid):
        state = self.pad(grid)
        state = self.background(state)
        self.state = state
        return state

    def set_level(self):
        if(self.levels):
            state = self.levels.new_level()
            try:
                self.env.unwrapped._setLevel(self.levels.path)
                self.levels.test(self.env)
                self.compiles = True
            except Exception as e:
                #print(e)
                self.compiles = False
                self.restart()
            except SystemExit:
                #print("SystemExit")
                self.compiles = False
                self.restart()
        else:
            lvl = random.randint(0,4)
            self.env.unwrapped._setLevel(lvl)
            self.env.reset()
            _, _, _, info = self.env.step(0)
            state = self.get_state(info['grid'])
        self.state = state
        return state
        
    def pad(self, state):
        pad_h = max(self.shape[-2] - state.shape[-2], 0)
        pad_w = max(self.shape[-1] - state.shape[-1], 0)
        pad_height = (pad_h//2, pad_h-pad_h//2)
        pad_width = (pad_w//2, pad_w-pad_w//2)
        padding = ((0,0), pad_height, pad_width)
        return np.pad(state, padding, 'constant')
        
    def background(self, state):
        background = 1 - np.clip(np.sum(state, 0), 0, 1)
        background = np.expand_dims(background, 0)
        return np.concatenate([state, background])

    def restart(self):
        self.env = gym_gvgai.make('gvgai-{}-lvl0-v0'.format(self.name))