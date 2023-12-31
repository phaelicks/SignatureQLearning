import importlib
from typing import Any, Dict, List

import gym
import numpy as np
import math

import abides_markets.agents.utils as markets_agent_utils
from abides_markets.orders import MarketOrder
from abides_markets.utils import dollarize
from abides_core import NanosecondTime
from abides_core.utils import str_to_ns
from abides_core.generators import ConstantTimeGenerator

from abides_gym.envs.markets_environment import AbidesGymMarketsEnv

class SubGymMarketsMarketMakingEnv_v2(AbidesGymMarketsEnv):
    """
    Market Making v0 environment, it defines a new ABIDES-Gym-markets environment.
    It provides an evironment for the problem of a market maker trying to maximize its 
    return by continuously posting (limit) buy and (limit) sell orders to capture the 
    spread while at the same time keeping its inventory low. The market maker starts 
    the day with cash but no position, then continously chooses bid and ask levels at 
    which to post limit orders.  At the end of the day all remaining inventory is liquidated.
    
    Arguments:
        - background_config: the handcrafted agents configuration used for the environnement
        - mkt_close: time the market day ends
        - timestep_duration: how long between 2 wakes up of the gym experimental agent
        - starting_cash: cash of the agents at the beginning of the simulation
        - order_fixed_size: size of the limit orders placed by the experimental gym agent
        - mkt_order_alpha: proportion of inventory for market orders placed by the experimental gym agent
        - state_history_length: length of the raw state buffer
        - market_data_buffer_length: length of the market data buffer
        - first_interval: how long the simulation is run before the first wake up of the gym experimental agent
        - observe_first_interval: if the gym agent observes market during first interval
        - max_inventory: absolute value of maximum inventory the experimental gym agent is allowed to accumulate
        - leftover_inventory_reward: a constant penalty per unit of inventory at market close
        - inventory_reward_dampener: parameter that defines dampening of rewards from speculation
        - reward_mode: can use a dense of sparse reward formulation
        - done_ratio: ratio (mark2market_t/starting_cash) that defines when an episode is done (if agent has lost too much mark to market value)
        - debug_mode: arguments to change the info dictionnary (lighter version if performance is an issue)
        - background_config_extra_kvargs: dictionary of extra key value  arguments passed to the background config builder function
    
    Market Maker V0:
        - Action Space:
            - [LMT BUY, LMT SELL] combinations of order_fixed_size: 
                {[], [] [], [], []} 
            - MKT BUY of mkt_order_alpha * inventory_t
            - MKT SELL of mkt_order_alpha * inventory_t
            - Do nothing
        - State Space:
            - remaining_time_pct
            - inventory_pct
            - mid_price
            - lagged_mid_price
            - imbalance_5
            – market_spread
    """

    # Decorator for functions to ignore buffering in market data and generl raw state
    raw_state_pre_process = markets_agent_utils.ignore_buffers_decorator
    raw_state_to_state_pre_process = (
        markets_agent_utils.ignore_mkt_data_buffer_decorator
    )

    def __init__(
            self,       
            background_config: Any = "rmsc04",
            mkt_close: str = "16:00:00",
            timestep_duration: str = "10s",
            starting_cash: int = 100_000,
            order_fixed_size: int = 100,
            mkt_order_alpha: float = 0.1,
            state_history_length: int = 3,  
            market_data_buffer_length: int = 5,
            first_interval: str = "00:15:00",
            observe_first_interval: bool = True,
            max_inventory: int = 1000,
            terminal_inventory_reward: int = 10, 
            inventory_reward_dampener: float = 0.,
            damp_mode: str = "asymmetric",
            reward_mode: str = "dense",
            done_ratio: float = 0.2,
            debug_mode: bool = False,
            background_config_extra_kvargs: Dict[str, Any] = {}
    ) -> None: 
        self.background_config: Any = importlib.import_module(
            "abides_markets.configs.{}".format(background_config), package=None
        )
        self.mkt_close: NanosecondTime = str_to_ns(mkt_close)
        self.timestep_duration: NanosecondTime = str_to_ns(timestep_duration)
        self.starting_cash: int = starting_cash
        self.order_fixed_size: int = order_fixed_size
        self.mkt_order_alpha: float = mkt_order_alpha
        self.state_history_length: int = state_history_length
        self.market_data_buffer_length: int = market_data_buffer_length
        self.first_interval: NanosecondTime = str_to_ns(first_interval)
        self.observe_first_interval: bool = observe_first_interval
        self.max_inventory: int = max_inventory
        self.terminal_inventory_reward: int = terminal_inventory_reward
        self.inventory_reward_dampener: float = inventory_reward_dampener
        self.damp_mode: str = damp_mode
        self.done_ratio: float = done_ratio
        self.debug_mode: bool = debug_mode

        # time the market is open
        self.mkt_open_duration: NanosecondTime = self.mkt_close - str_to_ns("09:30:00")

        # marked_to_market limit to STOP the episode
        self.down_done_condition: float = self.done_ratio * starting_cash

        # CHECK PROPERTIES
        assert background_config in [
            "rmsc03",
            "rmsc04",
            "smc_01",
        ], "Select rmsc03, rmsc04 or smc_01 as config"

        assert (self.mkt_close <= str_to_ns("16:00:00")) & (
            self.mkt_close >= str_to_ns("09:30:00")
        ), "Select authorized market hours"

        assert (
            self.timestep_duration <= self.mkt_open_duration) & (
            self.timestep_duration >= str_to_ns("00:00:00")
            ), "Select authorized timestep_duration"

        assert (type(self.starting_cash) == int) & (
            self.starting_cash >= 0
        ), "Select positive integer value for starting_cash" 

        assert (type(self.order_fixed_size) == int) & (
            self.order_fixed_size >= 0
        ), "Select positive integer value for order_fixed_size"

        assert (type(self.mkt_order_alpha) == float) & (
            0 <= self.mkt_order_alpha <= 1
        ), "Select positive float value for mkt_order_alpha between 0 and 1"

        assert (type(self.state_history_length) == int) & (
            self.state_history_length >= 0
        ), "Select positive integer value for order_fixed_size"

        assert (type(self.market_data_buffer_length) == int) & (
            self.market_data_buffer_length >= 0
        ), "Select positive integer value for order_fixed_size"

        assert (self.first_interval <= self.mkt_open_duration) & (
            self.first_interval >= str_to_ns("00:00:00")
        ), "Select authorized FIRST_INTERVAL delay"

        assert self.observe_first_interval in [
            True,
            False,
        ], "observe_first_interval needs to be True or False"  

        assert (type(self.max_inventory) == int) & (
            self.max_inventory >= 0
        ), "Select positive integer value for max_inventory"

        assert (
            type(self.terminal_inventory_reward) == int
        ), "Select integer value for terminal_inventory_reward"

        assert (type(self.inventory_reward_dampener) == float) & (
            0 <= self.inventory_reward_dampener <= 1
        ), "Select positive float value for inventory_reward_dampener between 0 and 1"

        assert damp_mode in [
            "asymmetric",
            "symmetric"
        ], "damp_mode needs to be symmetric or asymmetric"

        assert (type(self.done_ratio) == float) & (
            0 <= self.done_ratio <= 1
        ), "Select positive float value for done_ration between 0 and 1"

        assert self.debug_mode in [
            True,
            False,
        ], "debug_mode needs to be True or False"                

        # set observation interval
        if self.observe_first_interval:
            self.observation_interval: NanosecondTime = self.first_interval
            self.first_interval = str_to_ns("00:05:00") 
        else: 
            self.observation_interval: NanosecondTime = str_to_ns("00:00:00")

        # BACKGROUND CONFIG
        background_config_args = {"end_time": self.mkt_close}
        background_config_args.update(background_config_extra_kvargs)
        
        # INIT
        super().__init__(
            background_config_pair=(
                self.background_config.build_config,
                background_config_args,
            ),
            wakeup_interval_generator=ConstantTimeGenerator(
                step_duration=self.timestep_duration
            ),
            starting_cash=self.starting_cash,
            state_buffer_length=self.state_history_length,
            market_data_buffer_length=self.market_data_buffer_length,
            first_interval=self.first_interval, # length zero if observe_first_interval True
        )

        # ACTION SPACE
        # 9 LMT spreads order_fixed_size, 
        # MKT inventory * mkt_order_alpha
        # Do nothing
        self.num_actions: int = 4
        self.action_space: gym.Space = gym.spaces.Discrete(self.num_actions)
        self.do_nothing_action_id: int = self.num_actions - 1        

        # track spread for action translation
        self.spread: float = 0
        self.current_mid_price: int = 100_000
        self.previous_mid_price: int = 100_000

        # track inventory for MKT order action and reward calculation
        self.previous_inventory: int = 0
        self.current_inventory: int = 0        

        # dict with limit order spreads for actions
        # {action_ids : {bid multiplier, ask multiplier}}
        self.spread_multiplier_dict: Dict [int, Dict[str, float]] = {
            0: {"bid": 0.5, "ask": 0.5},
#            1: {"bid": 2, "ask": 2},
            1: {"bid": 0.5, "ask": 2},
            2: {"bid": 2, "ask": 0.5},
        }        

        # STATE SPACE
        # [remaining_time_pct, inventory_pct, mid_price, 
        #   lagged_mid_price, imbalance_3, market_spread]
        self.num_state_features: int = 2
        
        # create state space "box"
        self.state_highs: np.ndarray = np.array(
            [
                2, # remaining_time_pct
                2, # inventory_pct
            ],
            dtype=np.float32,
        ).reshape(self.num_state_features, 1)

        self.state_lows: np.ndarray = np.array(
            [
                -2, # remaining_time_pct
                -2, # inventory_pct
            ],
            dtype=np.float32,
        ).reshape(self.num_state_features, 1)

        self.observation_space: gym.Space = gym.spaces.Box(
            self.state_lows,
            self.state_highs,
            shape=(self.num_state_features, 1),
            dtype=np.float32
        )

        # REWARDS
        self.previous_cash = self.starting_cash


    # UTILITY FUNCTIONS that translate between gym environment and ABIDES simulation

    def _map_action_space_to_ABIDES_SIMULATOR_SPACE(
        self, action: int
    ) -> List[Dict[str, Any]]:
        """
        utility function that maps OpenAI action definition (integers) 
        to environnement API action definition (list of dictionaries)
        The action space ranges [0, 1, 2,] where:
        - `0` LMT order pairs of order_fixed_size at best bid ask level
        - `1` LMT order pairs of order_fixed_size at second best bid ask level
        - `2` MKT order of size -mkt_order_alpha * current_inventory
        - '3' DO NOTHING

        Arguments:
            - action: integer representation of the different actions

        Returns:
            - action_list: list of the corresponding series of action mapped into abides env apis
        """

        # limit orders
        if action in range(self.num_actions - 1):
            bid_multiplier = self.spread_multiplier_dict[action]["bid"] 
            ask_multiplier = self.spread_multiplier_dict[action]["ask"] 
            half_spread = self.spread / 2
            bid_price = round(self.current_mid_price - half_spread * bid_multiplier)
            ask_price = round(self.current_mid_price + half_spread * ask_multiplier)

            """
            print(f"unrounded bid price: {self.current_mid_price - half_spread * bid_multiplier}")
            print(f"rounded bid price {bid_price}")
            print(f"rounded ask price: {ask_price}")
            print(f"unrounded ask price: {self.current_mid_price + half_spread * ask_multiplier}")
            """
            
            cancel = {"type": "CCL_ALL"} # TODO: check order status, keep existing orders if on correct level
            lmt_buy = {
                "type": "LMT",
                "direction": "BUY",
                "size": self.order_fixed_size,
                "limit_price": bid_price
                # orderbook_dict filled in raw_state_to_state
            }
            lmt_sell = {
                "type": "LMT",
                "direction": "SELL",
                "size": self.order_fixed_size,
                "limit_price": ask_price
                # orderbook_dict filled in raw_state_to_state
            }
            
            if abs(self.current_inventory) < self.max_inventory:
                return [cancel, lmt_buy, lmt_sell]
            elif self.current_inventory > 0:
                return [cancel, lmt_sell]
            elif self.current_inventory < 0:
                return [cancel, lmt_buy]
            else:
                raise ValueError(
                    f"Current inventory {self.current_inventory} does not match allowed values"
                )
        elif action == self.do_nothing_action_id:
            return []
        else:
            raise ValueError(
                f"Action {action} is not part of the actions support by this environment."
            )              
        """            
        elif action == 3:
            if self.current_inventory == 0: 
                return []
            else:
                mkt_order_direction = "BUY" if self.current_inventory < 0 else "SELL" 
                mkt_order_size = math.ceil(abs(self.mkt_order_alpha * self.current_inventory))
                return [
                    {"type": "CCL_ALL"},
                    {
                        "type": "MKT",
                        "direction": mkt_order_direction,
                        "size": mkt_order_size
                    }
                ]
        """

    @raw_state_to_state_pre_process
    def raw_state_to_state(self, raw_state: Dict[str, Any]) -> np.ndarray:
        """
        method that transforms a raw state into a state representation

        Arguments:
            - raw_state: dictionnary that contains raw simulation information obtained from the gym experimental agent

        Returns:
            - state: state / observation representation for the market making v0 environnement
        """
                
        # 0)  Preliminary
        # 0) a) compute & save spread for action selection
        bids = raw_state["parsed_mkt_data"]["bids"]
        asks = raw_state["parsed_mkt_data"]["asks"]
        last_transactions = raw_state["parsed_mkt_data"]["last_transaction"]
        
        mid_prices = [
            markets_agent_utils.get_mid_price(b, a, lt)
            for (b, a, lt) in zip(bids, asks, last_transactions)
        ]
        best_bids = [
            bids[0][0] if len(bids) > 0 else mid
            for (bids, mid) in zip(bids, mid_prices)
        ]
        best_asks = [
            asks[0][0] if len(asks) > 0 else mid
            for (asks, mid) in zip(asks, mid_prices)
        ]

        spreads = np.array(best_asks) - np.array(best_bids)
        self.spread = spreads[-1]
        self.previous_mid_price = self.current_mid_price
        self.current_mid_price = mid_prices[-1]

        """
        # moving avergae of spreads:
        print("spread moving average: {}".format(spreads.mean()))
        print("current spread: {}".format(self.spread))
        """

        # 1) Timing
        mkt_open = raw_state["internal_data"]["mkt_open"][-1]
        mkt_close = raw_state["internal_data"]["mkt_close"][-1]
        current_time = raw_state["internal_data"]["current_time"][-1]
        assert (
            current_time >= mkt_open + self.first_interval
        ), "Agent has woken up earlier than its first interval"
        elapsed_time = current_time - mkt_open - self.first_interval
        total_time = mkt_close - mkt_open - self.first_interval
        # percentage time advancement
        time_pct = (total_time - elapsed_time) / total_time

        # 2) Inventory
        self.previous_inventory = self.current_inventory # save for reward calculation
        holdings = raw_state["internal_data"]["holdings"]
        self.current_inventory = holdings[-1] # save for mkt_order size
        inventory_pct = self.current_inventory / self.max_inventory

        # log custom metrics to tracker
        # TODO: implement custom metrics tracker

        # computed state
        computed_state = np.array(
            [
                time_pct,
                inventory_pct,
            ], dtype=np.float32
        )

        return computed_state.reshape(self.num_state_features, 1)

    @raw_state_pre_process
    def raw_state_to_reward(self, raw_state: Dict[str, Any]) -> float:
        """
        method that transforms a raw state into the reward obtained during the step

        Arguments:
            - raw_state: dictionnary that contains raw simulation information obtained from the gym experimental agent

        Returns:
            - reward: immediate reward computed at each step  for the execution v0 environnement
        """
        # we define the reward as sum of two components (Spooner et al (2018)):
        #   1) value of executed orders since last step
        #   2) change in inventory value due to midprice fluctuations
        # TODO: add spread as reward component instead of state variable

        # 1) change in cash value
        cash = raw_state["internal_data"]["cash"]
        pnl = (cash - self.previous_cash) / 100 # in dollar terms
        self.pnl = pnl / self.order_fixed_size
        self.previous_cash = cash
        
        # 2) change in inventory value
        mid_price_change = (self.current_mid_price - self.previous_mid_price) / 100
        inventory_reward = self.previous_inventory * mid_price_change / self.max_inventory
        # damp reward component
        if self.damp_mode == "symmetric":
            inventory_reward *= (1 - self.inventory_reward_dampener)
        elif self.damp_mode == "asymmetric":
            inventory_reward -= max(
                0,
                self.inventory_reward_dampener * self.previous_inventory * mid_price_change / self.max_inventory
            )
        self.inventory_reward = inventory_reward
        
        # TODO: normalize for order size and max inventory?
        #reward = pnl / self.order_fixed_size + inventory_change / self.max_inventory
        
        reward = inventory_reward # + pnl
        return reward

    @raw_state_pre_process
    def raw_state_to_update_reward(self, raw_state: Dict[str, Any]) -> float:

        # 1) inventory pct
        inventory = raw_state["internal_data"]["holdings"]
        inventory_pct = inventory / self.max_inventory
        inventory_pct = abs(inventory_pct) if abs(inventory_pct) <= 1 else 1

        # 2) Last Known Market Transaction Price
        last_transaction = raw_state["parsed_mkt_data"]["last_transaction"]

        # 3) Calculate update reward
        update_reward = (1 - inventory_pct) ** 2 * self.terminal_inventory_reward
        
        return update_reward

    @raw_state_pre_process
    def raw_state_to_done(self, raw_state: Dict[str, Any]) -> bool:
        """
        method that transforms a raw state into the flag if an episode is done

        Arguments:
            - raw_state: dictionnary that contains raw simulation information obtained from the gym experimental agent

        Returns:
            - done: flag that describes if the episode is terminated or not  for the execution v0 environnement
        """
        # episode can stop because market closes (or because some condition is met)
        # here no other condition is used (such as running out of cash)
        return

    @raw_state_pre_process
    def raw_state_to_info(self, raw_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        method that transforms a raw state into an info dictionnary

        Arguments:
            - raw_state: dictionnary that contains raw simulation information obtained from the gym experimental agent

        Returns:
            - reward: info dictionnary computed at each step for the daily investor v0 environnement
        """
        # Agent cannot use this info for taking decision
        # only for debugging

        if not self.debug_mode:
            cash = raw_state["internal_data"]["cash"]
            holdings = raw_state["internal_data"]["holdings"]
            return {
                "pnl": self.pnl,
                "cash": cash,
                "inventory": holdings,
                "inventory_reward": self.inventory_reward,
                "mid_price": self.current_mid_price
            }

        # 1) Last Known Market Transaction Price
        last_transaction = raw_state["parsed_mkt_data"]["last_transaction"]

        # 2) Last Known best bid
        bids = raw_state["parsed_mkt_data"]["bids"]
        best_bid = bids[0][0] if len(bids) > 0 else last_transaction

        # 3) Last Known best ask
        asks = raw_state["parsed_mkt_data"]["asks"]
        best_ask = asks[0][0] if len(asks) > 0 else last_transaction

        # 4) Available Cash
        cash = raw_state["internal_data"]["cash"]

        # 5) Current Time
        current_time = raw_state["internal_data"]["current_time"]

        # 6) Holdings
        holdings = raw_state["internal_data"]["holdings"]

        # 7) Spread
        spread = best_ask - best_bid

        # 8) OrderBook features
        orderbook = {
            "asks": {"price": {}, "volume": {}},
            "bids": {"price": {}, "volume": {}},
        }

        for book, book_name in [(bids, "bids"), (asks, "asks")]:
            for level in [0, 1, 2]:
                price, volume = markets_agent_utils.get_val(book, level)
                orderbook[book_name]["price"][level] = np.array([price]).reshape(-1)
                orderbook[book_name]["volume"][level] = np.array([volume]).reshape(-1)

        # 9) order_status
        order_status = raw_state["internal_data"]["order_status"]

        # 10) mkt_open
        mkt_open = raw_state["internal_data"]["mkt_open"]

        # 11) mkt_close
        mkt_close = raw_state["internal_data"]["mkt_close"]

        # 12) last vals
        last_bid = markets_agent_utils.get_last_val(bids, last_transaction)
        last_ask = markets_agent_utils.get_last_val(asks, last_transaction)

        # 13) spreads
        wide_spread = last_ask - last_bid
        ask_spread = last_ask - best_ask
        bid_spread = best_bid - last_bid

        # 14) compute the marked to market
        marked_to_market = cash + holdings * last_transaction

        # 15) self.pnl
        # 16) self.inventory_reward
        # 17) reward = self.pnl + self.inventory_reward

        if self.debug_mode == True:
            return {
                "last_transaction": last_transaction,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "bids": bids,
                "asks": asks,
                "cash": cash,
                "current_time": current_time,
                "inventory": holdings,
                "orderbook": orderbook,
                "order_status": order_status,
                "mkt_open": mkt_open,
                "mkt_close": mkt_close,
                "last_bid": last_bid,
                "last_ask": last_ask,
                "wide_spread": wide_spread,
                "ask_spread": ask_spread,
                "bid_spread": bid_spread,
                "marked_to_market": marked_to_market,
                "pnl": self.pnl,
                "inventory_reward": self.inventory_reward,
                "reward": self.pnl + self.inventory_reward,
            }
        else:
            return {
                "inventory": holdings,
            }   

    def close(self) -> None:
        """
        Closes the environment and performs necassary clean up such as setting internal
        variables to initial value for next reset call.
        """    
        # set internal variables to default values for next episode
        self.current_mid_price = 100_000
        self.previous_mid_price = 100_000
        self.current_inventory = 0
        self.previous_inventory = 0
        self.previous_cash = self.starting_cash

        return





    

