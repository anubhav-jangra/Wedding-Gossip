import random
import functools
from copy import copy
import numpy as np

from gymnasium.spaces import MultiDiscrete

from pettingzoo import ParallelEnv
from pettingzoo.utils import agent_selector, wrappers
from pettingzoo.test import parallel_api_test
from collections import defaultdict

# Action Constants
LISTEN_L = 0
LISTEN_R = 1
TALK_L = 2
TALK_R = 3
MOVE = 4
# Observation Constants (0-3 same as action)
NONE = 4

# Reward Params
ALPHA = 1
BETA = 1
GAMMA = 2
DELTA = 1

# Truncation condition
N_TURNS = 1000

class WeddingGossipEnvironment(ParallelEnv):
    metadata = {"render_modes": ["human"], 
                "name": "wedding_gossip_environment_v0"}

    def __init__(self, render_mode=None):
        self.possible_agents = ["player_" + str(r) for r in range(90)]
        self.agents = copy(self.possible_agents)
        self._agent_selector = agent_selector(self.agents)

        # optional: a mapping between agent name and player_ID
        self.agent_name_mapping = dict(
            zip(self.possible_agents, 
                list(range(len(self.possible_agents)))
            )
        )
        self.render_mode = render_mode
        
        self.observation_spaces = dict(
            zip(
                self.agents,
                [
                    MultiDiscrete(np.array([[100 for _ in range(90)], [2 for _ in range(90)], [5 for _ in range(90)]]))
                ]
                * 90,
            )
        )

        self.action_spaces = dict(
            zip(
                self.agents,
                [
                    MultiDiscrete(np.array([5, 90, 10]))
                ]
                * 90,
            )
        )

    def reset(self, seed=None, options=None):
        """
        Reset needs to initialize the `agents` attribute and must set up the
        environment so that render(), and step() can be called without issues.
        Here it initializes the `num_moves` variable which counts the number of
        hands that are played.
        Returns the observations for each agent
        """
        self.timestep = 0

        # a hash mapping player_ids to where they are situated on the board
        # each player_id is mapped to a number from 0-99. 
        # table_num = value % 10 (0-9)
        # seat_num = value / 10 (0-9)
        self.pos = None
        self.pos = random.sample(range(100), k=90)
        # update the self.seating list - 100 size list based on pos
        self.seating = None
        self._init_seating()

        # Not sure if we need this
        # self.available_seats = self._get_available_seats()

        self.agent_gossips = [[] for _ in range(90)]

        init_obs = np.array([self.pos, [0 for _ in range(90)], [4 for _ in range(90)]])
        observations = {a: copy(init_obs) for a in self.agents}
        gossip = random.sample(range(90), 90)
        for i, a in enumerate(observations.keys()):
            observations[a][1][gossip[i]] = 1
            self.agent_gossips[self.agent_name_mapping[a]].append(gossip[i])

        # Get dummy infos. Necessary for proper parallel_to_aec conversion
        infos = {a: {} for a in self.agents}

        self.state = observations

        return observations, infos

    def step(self, actions):
        """
        step(action) takes in an action for each agent and should return the
        - observations
        - rewards
        - terminations
        - truncations
        - infos
        dicts where each dict looks like {agent_1: item_1, agent_2: item_2}
        """
        # If a user passes in actions with no agents, then just return empty observations, etc.
        if not actions:
            self.agents = []
            return {}, {}, {}, {}, {}

        rewards = {}
        """ reward function
        listen gossip i: + i * ALPHA
        talk gossip i: + i * num_nods - num_shake * BETA
        move: +GAMMA if success else -DELTA
        end: 1000000 / timestep (everyone has all gossip)
        """

        obs_actions = [4 for _ in range(90)]
        feedback = [[] for _ in range(90)]
        moves = []
        
        # process listens -> talk -> move in order
        for agent, action in sorted(actions.items(), key=(lambda x: x[1][0])):
            # get action 
            act, goss, seat = action
            aid = self.agent_name_mapping[agent]

            rewards[agent] = 0
            obs_actions[aid] = act

            # listen
            if act < 2:
                neighbors = self._get_left_neighbors(aid) if act == 0 else self._get_right_neighbors(aid)

                possible_gossips = []
                for n in neighbors:
                    n_act, n_goss, _ = actions["player_" + str(n)]
                    complement = 2 if act == 1 else 3
                    if (n_act == complement and n_goss in self.agent_gossips[n] and n_goss not in self.agent_gossips[aid]):
                        possible_gossips.append((n_goss, n))
                heard_gossip = max(possible_gossips)[0] if possible_gossips else -1
                if heard_gossip > 0:
                    self.agent_gossips[aid].append(heard_gossip)
                for g, i in possible_gossips:
                    feedback[i].append((g == heard_gossip))

                rewards[agent] += (heard_gossip + 1) * ALPHA
            # talk 
            elif act < 4:
                # check if gossip to share is present in the player's gossip list
                if goss not in self.agent_gossips[aid]:
                    # penalty can't be too big - will discourage talking
                    rewards[agent] -= 10
                else:
                    # reward for choosing valid gossip
                    rewards[agent] += 10
                    # check for confirmation! If the said gossip was well-received, then give positive reward!
                    for f in feedback[aid]:
                        rewards[agent] += goss if f else -BETA
            # move
            else:
                move_choice_order = []
                move_choice_order.append(seat)
                # fill out the rest - 
                for i in random.sample(range(10), 10):
                    if i not in move_choice_order:
                        move_choice_order.append(i)
                moves.append((aid, move_choice_order))

        # resolve moves
        random.shuffle(moves)
        for aid, pref in moves:
            rewards["player_" + str(aid)] += GAMMA if self._move_player(aid, pref) else - DELTA

        observations = {}
        for a in self.agents:
            aid = self.agent_name_mapping[a]
            gossips = [(1 if g in self.agent_gossips[aid] else 0) for g in range(90)]
            tbl = aid // 10
            tbl_actions = [(obs_actions[self.agent_name_mapping[n]] if self.pos[self.agent_name_mapping[n]] // 10 == tbl else 4) for n in self.agents]
            observations[a] = np.array([self.pos, gossips, tbl_actions])

        self.state = observations

        # Check termination conditions
        terminations = {agent: False for agent in self.agents}

        # Check truncation conditions (overwrites termination conditions)
        truncations = {a: (self.timestep > N_TURNS) for a in self.agents}

        # Exit condition
        if any(terminations.values()) or all(truncations.values()):
            self.agents = []

        infos = {a: {} for a in self.agents}
        
        self.render()

        return observations, rewards, terminations, truncations, infos

    def render(self):
        print("===== SEATING / MOVES =====")
        for i in range(9):
            table = [(self.seating[j], self.state["player_" + str(j)][2][j]) for j in range(i*10,(i+1)*10)]
            print(f'Table {i}: {table}')
        print("===== GOSSIP INVENTORY =====")
        for i, g in enumerate(self.agent_gossips):
            print(f"Player {i}: {g}")
    
    def close(self):
        pass

    @functools.lru_cache(maxsize=None)
    def observation_space(self, agent):
        """
            array([seating, gossip, table actions])
        """
        return self.observation_spaces[agent]

    @functools.lru_cache(maxsize=None)
    def action_space(self, agent):
        """
            array([action, gossip, seat1, seat2])
        """
        return self.action_spaces[agent]
   
    # Do we need this?
    def _get_available_seats(self):
        """
            A function that looks at self.pos, and returns a tuple of size 10 containing the available seats.
        """
        return set(range(100)).difference(set(self.pos))
    
    def _init_seating(self):
        # a function to create and update the self.seating list - 100 size list. None represents empty seat!
        self.seating = [None for _ in range(100)]
        for pid, seat in enumerate(self.pos):
            self.seating[seat] = pid

    def _get_left_neighbors(self, seat_id, _count=3):
        if _count > 9:
            print("CANT HAVE MORE THAN 9 LEFT NEIGHBORS!")
        
        neighbor_ids = []
        for i in range(1, _count+1):
            curr_seat_id = (seat_id // 10 * 10) + ((seat_id - i) % 10)
            if self.seating[curr_seat_id]:
                neighbor_ids.append(self.seating[curr_seat_id])
        return neighbor_ids

    def _get_right_neighbors(self, seat_id, _count=3):
        if _count > 9:
            print("CANT HAVE MORE THAN 9 RIGHT NEIGHBORS!")
        
        neighbor_ids = []
        for i in range(1, _count+1):
            curr_seat_id = (seat_id // 10 * 10) + ((seat_id + i) % 10)
            if self.seating[curr_seat_id]:
                neighbor_ids.append(self.seating[curr_seat_id])
        return neighbor_ids

    def _move_player(self, aid, priority_list):
        curr_seat = self.pos[aid]

        if len(priority_list) > 10:
            return False 

        for move in priority_list:
            # check if new position is occupied
            if not self.seating[move]:
                # move player from old position to new position
                self.seating[curr_seat] = None
                self.seating[move] = aid

                # update player
                self.pos[aid] = move

                return True

        return False 

if __name__ == "__main__":
    env = WeddingGossipEnvironment()
    parallel_api_test(env, num_cycles=1_000_000)
