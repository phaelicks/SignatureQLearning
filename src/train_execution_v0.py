import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from collections import deque
import sys
import tqdm
from copy import deepcopy
from typing import Optional, Dict, List
from math import floor


def train(
        env, 
        policy, 
        episodes, 
        discount: float = 0.99,
        learning_rate: float = 0.1, 
        learning_rate_decay = lambda step: 1,
        epsilon: float = 0.2, 
        epsilon_decay = lambda step: 1,
        window_length: Optional[int] = None,
        printing: bool = False
) -> Dict[str, List]: 
    initial_epsilon: float = epsilon 
    loss_history = []
    reward_history = []
    cash_history = []
    terminal_inventory = []
    intermediate_policies = []   
    action_history = []
    inventory_history = []

    # CHECK PROPERTIES
    if window_length != None:
        assert window_length > 0, "History window length must be a positive integer."
    
    # GRADIENT DESCENT DETAILS
    #loss_fn = nn.SmoothL1Loss() 
    loss_fn = nn.MSELoss()

    optimizer = optim.Adam(policy.parameters(), lr=learning_rate)
    scheduler = optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=learning_rate_decay)
    #scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.995)
    
    # compute first_interval steps to solely observe
    do_nothing_steps = 0
    #(
    #    floor(env.observe_interval / env.timestep_duration)
    #)

    # TRAINING
    pbar = tqdm.trange(episodes, file=sys.stdout)
    for episode in pbar:
        episode_loss = 0
        episode_reward = 0
        episode_actions = []
        episode_inventory = []

        observation = env.reset()[(0,1,3,5),0] # as row vector, gym 0.18.0 returns only state at reset
        action = 2 # do nothing
        episode_actions.append(action)

        history = deque(maxlen=window_length)
        history.append(observation)

        next_observation_tensor = torch.tensor(
            [observation], requires_grad=False, dtype=torch.float
        )

        # initialize signature variable
        history_signature = policy.update_signature(next_observation_tensor.unsqueeze(0))
        last_observation_tensor = next_observation_tensor

        done = False
        step_counter = 0
        do_nothing_counter = 0

        # RUN EPISODE
        while not done:
            if do_nothing_counter < do_nothing_steps:
                # agent only observes 
                action = 2 # next action is 'do nothing'
                episode_actions.append(action)

                observation, _, _, _ = env.step(action)
                observation = observation[(0,1,3,5),0]
                last_observation_tensor = torch.tensor(
                    [observation], requires_grad=False, dtype=torch.float
                )

                history.append(observation)
                do_nothing_counter += 1

                continue

            if do_nothing_counter == do_nothing_steps:
                # calculate first signature for observed history so far
                history_signature = policy.update_signature(
                    torch.tensor(history, requires_grad=False, dtype=torch.float).unsqueeze(0)
                )                
            
            # create Q values and select action
            Q = policy(history_signature)[0]
            if np.random.rand(1) < epsilon:
                action = np.random.randint(0, env.action_space.n)
                print("random")
            else:
                _, action = torch.max(Q, -1)
                action = action.item()
                print("Q max")
            episode_actions.append(action)

            # take action
            observation, reward, done, info = env.step(action)
            observation = observation[(0,1,3,5),0]
            if printing:
                print("reward: {} | pnl: {} | inventory {}".format(
                    reward, info["pnl"], info["holdings"]
                )
                )
                if done: 
                    print("update reward {}".format(reward - info["pnl"]))

            episode_inventory.append(info["holdings"])                

            # update history and signature
            history.append(observation) # pops left if maxlen != None
            next_observation_tensor = torch.tensor(
                    [observation], requires_grad=False, dtype=torch.float
            )

            if window_length == None:
                new_path = torch.cat((last_observation_tensor, next_observation_tensor), 0).unsqueeze(0)
                history_signature = policy.update_signature(
                    new_path, last_observation_tensor, history_signature
                )
            else: 
                history_signature = policy.update_signature(
                    torch.tensor(history, requires_grad=False, dtype=torch.float).unsqueeze(0)
                )
            # TODO: find way to compute signature of shortened path via Chen

            Q_target = torch.tensor(reward, requires_grad=False, dtype=torch.float)            
            if not done:
                Q1 = policy(history_signature)[0]
                maxQ1, _ = torch.max(Q1, -1)
                Q_target += torch.mul(maxQ1, discount)
            Q_target.detach_()
            
            #print(Q[action])
            loss = loss_fn(Q[action], Q_target)
            if printing:
                print("loss:", loss)
            policy.zero_grad()
            loss.backward()
            # clip gradient to improve robustness
            #nn.utils.clip_grad_value_(policy.parameters(), 1)
            #nn.utils.clip_grad_norm_(policy.parameters(), 0.25, 2)
            optimizer.step()         
            
            episode_loss += loss.item()
            episode_reward += reward
            step_counter += 1

            # take steps
            scheduler.step()
            epsilon = initial_epsilon * epsilon_decay(step_counter)

            if done:
                terminal_inventory.append(info["holdings"])
            else:
                last_observation_tensor = next_observation_tensor
            
            if done or step_counter % 100 == 0:
                print(
                    "\n Episode {} | step {} | reward {} | loss {}".format(
                        episode, step_counter, episode_reward, episode_loss
                    )
                )
                print("Q values:", Q)

        env.close()

        # Record history
        loss_history.append(episode_loss)
        reward_history.append(episode_reward)
        #if (episode+1) % 5 == 0 or episode == 0:
        #    action_history.append(episode_actions)
        #    inventory_history.append(episode_inventory)
        action_history.append(episode_actions)
        inventory_history.append(episode_inventory)

        if (episode+1) % 10 == 0:
            policy_copy = deepcopy(policy.state_dict())
            intermediate_policies.append(policy_copy)

        if (episode+1) % 1000 == 0:
            optimizer = optim.Adam(policy.parameters(), lr=learning_rate)
            scheduler = optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=learning_rate_decay)
            #scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=0.999)

    results = {
        "rewards": reward_history,
        "losses": loss_history,
        "cash": cash_history,
        "terminal_inventory": terminal_inventory,
        "actions": action_history,
        "inventories": inventory_history,
        "history": history,
        "intermediate": intermediate_policies,
    }
    return results






            



                  