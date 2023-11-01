from utils.config import set_np_formatting, set_seed, get_args, parse_sim_params, load_cfg
from utils.parse_task import parse_task

from rl_games.algos_torch import torch_ext
from rl_games.common import env_configurations, vecenv
from rl_games.common.algo_observer import AlgoObserver
from rl_games.torch_runner import Runner

from learning import amp_agent
from learning import amp_players
from learning import amp_models
from learning import amp_network_builder

from learning import anyskill_network_builder
from learning import anyskill_players
from learning import anyskill_agent
from learning import spec_anyskill_agent
from learning import spec_anyskill_players
from learning import spec_anyskill_network_builder

# from learning import hrl_conditioned_agent
# from learning import hrl_fsm_players
# from learning import hrl_agent
# from learning import hrl_players
# from learning import hrl_network_builder

from learning import calm_agent
from learning import calm_players
from learning import calm_models
from learning import calm_network_builder
from learning import hrl_models

from env.tasks import humanoid_amp_task

import datetime

try:
    import wandb
except:
    wandb = None

args = None
cfg = None
cfg_train = None
run_name = None


def create_rlgpu_env(**kwargs):
    use_horovod = cfg_train['params']['config'].get('multi_gpu', False)
    if use_horovod:
        import horovod.torch as hvd

        rank = hvd.rank()
        print("Horovod rank: ", rank)

        cfg_train['params']['seed'] = cfg_train['params']['seed'] + rank

        args.device = 'cuda'
        args.device_id = rank
        args.rl_device = 'cuda:' + str(rank)

        cfg['rank'] = rank
        cfg['rl_device'] = 'cuda:' + str(rank)

    sim_params = parse_sim_params(args, cfg, cfg_train)
    task, env = parse_task(args, cfg, cfg_train, sim_params)

    print('num_envs: {:d}'.format(env.num_envs))
    print('num_actions: {:d}'.format(env.num_actions))
    print('num_obs: {:d}'.format(env.num_obs))
    print('num_states: {:d}'.format(env.num_states))

    frames = kwargs.pop('frames', 1)
    if frames > 1:
        env = wrappers.FrameStack(env, frames, False)
    return env


class RLGPUAlgoObserver(AlgoObserver):
    def __init__(self, use_successes=True):
        self.use_successes = use_successes
        return

    def after_init(self, algo):
        self.algo = algo
        self.consecutive_successes = torch_ext.AverageMeter(1, self.algo.games_to_track).to(self.algo.ppo_device)
        self.writer = self.algo.writer
        return

    def process_infos(self, infos, done_indices):
        if isinstance(infos, dict):
            if (self.use_successes == False) and 'consecutive_successes' in infos:
                cons_successes = infos['consecutive_successes'].clone()
                self.consecutive_successes.update(cons_successes.to(self.algo.ppo_device))
            if self.use_successes and 'successes' in infos:
                successes = infos['successes'].clone()
                self.consecutive_successes.update(successes[done_indices].to(self.algo.ppo_device))
        return

    def after_clear_stats(self):
        self.mean_scores.clear()
        return

    def after_print_stats(self, frame, epoch_num, total_time):
        if self.consecutive_successes.current_size > 0:
            mean_con_successes = self.consecutive_successes.get_mean()
            self.writer.add_scalar('successes/consecutive_successes/mean', mean_con_successes, frame)
            self.writer.add_scalar('successes/consecutive_successes/iter', mean_con_successes, epoch_num)
            self.writer.add_scalar('successes/consecutive_successes/time', mean_con_successes, total_time)
        return


class RLGPUEnv(vecenv.IVecEnv):
    def __init__(self, config_name, num_actors, **kwargs):
        self.env = env_configurations.configurations[config_name]['env_creator'](**kwargs)
        self.use_global_obs = (self.env.num_states > 0)

        self.full_state = {}
        self.full_state["obs"] = self.reset()
        if self.use_global_obs:
            self.full_state["states"] = self.env.get_state()
        return

    def step(self, action):
        next_obs, reward, is_done, info = self.env.step(action)
        self.full_state["obs"] = next_obs
        if self.use_global_obs:
            self.full_state["states"] = self.env.get_state()
            return self.full_state, reward, is_done, info
        else:
            return self.full_state["obs"], reward, is_done, info

    def reset(self, env_ids=None):
        self.full_state["obs"] = self.env.reset(env_ids)
        if self.use_global_obs:
            self.full_state["states"] = self.env.get_state()
            return self.full_state
        else:
            return self.full_state["obs"]

    def get_number_of_agents(self):
        return self.env.get_number_of_agents()

    def get_env_info(self):
        info = {}
        info['action_space'] = self.env.action_space
        info['observation_space'] = self.env.observation_space
        info['amp_observation_space'] = self.env.amp_observation_space
        info['enc_amp_observation_space'] = self.env.enc_amp_observation_space

        if isinstance(self.env.task, humanoid_amp_task.HumanoidAMPTask):
            info['task_obs_size'] = self.env.task.get_task_obs_size()
        else:
            info['task_obs_size'] = 0

        if self.use_global_obs:
            info['state_space'] = self.env.state_space
            print(info['action_space'], info['observation_space'], info['state_space'])
        else:
            print(info['action_space'], info['observation_space'])

        return info


vecenv.register('RLGPU', lambda config_name, num_actors, **kwargs: RLGPUEnv(config_name, num_actors, **kwargs))
env_configurations.register('rlgpu', {
    'env_creator': lambda **kwargs: create_rlgpu_env(**kwargs),
    'vecenv_type': 'RLGPU'})


def build_alg_runner(algo_observer):
    runner = Runner(algo_observer)
    runner.algo_factory.register_builder('amp', lambda **kwargs: amp_agent.AMPAgent(**kwargs))
    runner.player_factory.register_builder('amp', lambda **kwargs: amp_players.AMPPlayerContinuous(**kwargs))
    runner.model_builder.model_factory.register_builder('amp', lambda network, **kwargs: amp_models.ModelAMPContinuous(network))
    runner.model_builder.network_factory.register_builder('amp', lambda **kwargs: amp_network_builder.AMPBuilder())

    # use the llc_checkpoint
    runner.algo_factory.register_builder('hrl', lambda **kwargs: hrl_agent.HRLAgent(**kwargs))
    runner.player_factory.register_builder('hrl', lambda **kwargs: hrl_players.HRLPlayer(**kwargs))
    runner.model_builder.model_factory.register_builder('hrl', lambda network, **kwargs: hrl_models.ModelHRLContinuous(network))
    runner.model_builder.network_factory.register_builder('hrl', lambda **kwargs: hrl_network_builder.HRLBuilder())

    runner.model_builder.model_factory.register_builder('gen_anyskill', lambda network, **kwargs: hrl_models.ModelHRLContinuous(network))
    runner.algo_factory.register_builder('gen_anyskill', lambda **kwargs: anyskill_agent.AnyskillAgent(**kwargs))
    runner.model_builder.network_factory.register_builder('gen_anyskill', lambda **kwargs: anyskill_network_builder.AnyskillBuilder())
    runner.player_factory.register_builder('gen_anyskill', lambda **kwargs: anyskill_players.AnyskillPlayer(**kwargs))

    runner.model_builder.model_factory.register_builder('spec_anyskill', lambda network, **kwargs: hrl_models.ModelHRLContinuous(network))
    runner.algo_factory.register_builder('spec_anyskill', lambda **kwargs: spec_anyskill_agent.SpecAnyskillAgent(**kwargs))
    runner.model_builder.network_factory.register_builder('spec_anyskill', lambda **kwargs: spec_anyskill_network_builder.SpecAnyskillBuilder())
    runner.player_factory.register_builder('spec_anyskill', lambda **kwargs: spec_anyskill_players.SpecAnyskillPlayer(**kwargs))

    runner.algo_factory.register_builder('calm', lambda **kwargs: calm_agent.CALMAgent(**kwargs))
    runner.player_factory.register_builder('calm', lambda **kwargs: calm_players.CALMPlayer(**kwargs))
    runner.model_builder.model_factory.register_builder('calm', lambda network, **kwargs: calm_models.ModelCALMContinuous(network))
    runner.model_builder.network_factory.register_builder('calm', lambda **kwargs: calm_network_builder.CALMBuilder())

    return runner


def main():
    global args
    global cfg
    global cfg_train
    global run_name

    set_np_formatting()
    args = get_args()
    cfg, cfg_train, logdir = load_cfg(args)

    time_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # run_name = f"{args.wandb_run_name}_{time_str}"
    assert args.track and wandb or not args.track, "Tracking requires wandb to be installed."

    cfg_train['params']['seed'] = set_seed(cfg_train['params'].get("seed", -1), cfg_train['params'].get("torch_deterministic", False))

    if args.horovod:
        cfg_train['params']['config']['multi_gpu'] = args.horovod

    if args.horizon_length != -1:
        cfg_train['params']['config']['horizon_length'] = args.horizon_length

    if args.minibatch_size != -1:
        cfg_train['params']['config']['minibatch_size'] = args.minibatch_size

    if args.motion_file:
        cfg['env']['motion_file'] = args.motion_file

    # Create default directories for weights and statistics
    cfg_train['params']['config']['train_dir'] = args.output_path

    cfg['env']['text_file'] = args.text_file
    cfg_train['params']['config']['text_file'] = args.text_file
    cfg_train['params']['config']['render'] = args.render
    cfg_train['params']['config']['wandb_counter'] = args.wandb_counter
    cfg_train['params']['config']['headless'] = args.headless
    cfg['env']['render'] = args.render
    cfg['env']['wandb_counter'] = args.wandb_counter
    run_name = f"{args.render}_{str(args.wandb_counter)}_{time_str}"

    if args.track:
        wandb.init(
            project=args.wandb_project_name,
            sync_tensorboard=True,
            config=args,
            monitor_gym=True,
            save_code=True,
            name=run_name,
        )

    vargs = vars(args)

    algo_observer = RLGPUAlgoObserver()

    runner = build_alg_runner(algo_observer)
    runner.load(cfg_train)
    runner.reset()
    runner.run(vargs)

    return


if __name__ == '__main__':
    main()
