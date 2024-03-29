# author: 汪奕航
import os
import json
from argparse import Namespace 
import numpy as np
import torch
import torch.nn as nn
from utils import *
from model import *
from criterion import PackedCrossEntropyLoss
import wandb
from metrics.calc_metric import metric_logger
# os.environ["WANDB_API_KEY"] = "6b6ae714ca6908898fec2f0198691c5e2a52b7f7"
# os.environ["WANDB_MODE"] = "offline"

wandb.init(project="imagecaption", name="WangModel")

dataset = 'deepfashion'
# 设置模型超参数和辅助变量
config = Namespace(
    max_len = 100,
    captions_per_image = 1,
    batch_size = 32,
    image_code_dim = 2048,
    word_dim = 512,
    hidden_size = 512,
    attention_dim = 512,
    num_layers = 1,
    encoder_learning_rate = 0.0001,
    decoder_learning_rate = 0.0005,
    num_epochs = 10,
    grad_clip = 5.0,
    alpha_weight = 1.0,
    evaluate_step = 900, # 每隔多少步在验证集上测试一次
    checkpoint = None, # 如果不为None，则利用该变量路径的模型继续训练
    best_checkpoint = f'../model/WangModel/best_{dataset}.ckpt', # 验证集上表现最优的模型的路径
    last_checkpoint = f'../model/WangModel/last_{dataset}.ckpt', # 训练完成时的模型的路径
    beam_k = 5
)

wandb.config.update(config)
metriclogger = metric_logger(wandb)

if not os.path.exists('../model/WangModel'):
    os.makedirs('../model/WangModel')

# 设置GPU信息
os.environ['CUDA_VISIBLE_DEVICES'] = '0'
device = torch.device("cuda" if torch.cuda.is_available() else "cpu") 

# 数据
data_dir = f'../data/{dataset}/'
vocab_path = f'../data/{dataset}/vocab.json'
train_loader, valid_loader, test_loader = mktrainval(data_dir, vocab_path, config.batch_size, workers=0)

# 模型
with open(vocab_path, 'r') as f:
    vocab = json.load(f)

# 随机初始化 或 载入已训练的模型
start_epoch = 0
checkpoint = config.checkpoint
if checkpoint is None:
    model = WangModel(config.image_code_dim, vocab, config.word_dim, config.hidden_size, config.num_layers)
else:
    checkpoint = torch.load(checkpoint)
    start_epoch = checkpoint['epoch'] + 1
    model = checkpoint['model']

# 优化器
optimizer = get_optimizer(model, config)

# 将模型拷贝至GPU，并开启训练模式
model.to(device)
model.train()

# 损失函数
loss_fn = PackedCrossEntropyLoss().to(device)

best_rougel, best_cider, best_meteor, best_bleu = 0, 0, 0, 0
print("开始训练")

global_step = 0
for epoch in range(start_epoch, config.num_epochs):
    now_step = 0
    for i, (imgs, caps, caplens) in enumerate(train_loader):
        optimizer.zero_grad()
        # 1. 读取数据至GPU
        imgs = imgs.to(device)
        caps = caps.to(device)
        caplens = caplens.to(device)

        # 2. 前馈计算
        predictions, sorted_captions, lengths, sorted_cap_indices = model(imgs, caps, caplens)
        # 3. 计算损失
        # captions从第2个词开始为targets
        loss = loss_fn(predictions, sorted_captions[:, 1:], lengths)
        loss.backward()
        # 梯度截断
        if config.grad_clip > 0:
            nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
        
        # 4. 更新参数
        optimizer.step()
        wandb.log({"loss": loss.cpu()})

        now_step += 1
        global_step += 1
        
    state = {
            'epoch': epoch,
            'step': now_step,
            'model': model,
            'optimizer': optimizer
            }
        # if (i+1) % config.evaluate_step == 0:
    rouge_l, cider, meteor, bleu = evaluate(valid_loader, model, config, metriclogger, global_step)
    # 5. 选择模型
    print(rouge_l, cider, meteor, bleu)
    print(best_rougel, best_cider, best_meteor, best_bleu)
    if np.mean([rouge_l, cider, meteor, bleu]) > np.mean([best_rougel, best_cider, best_meteor, best_bleu]):
        best_rougel, best_cider, best_meteor, best_bleu = rouge_l, cider, meteor, bleu
        torch.save(state, config.best_checkpoint)

    torch.save(state, config.last_checkpoint)

checkpoint = torch.load(config.best_checkpoint)
model = checkpoint['model']
rouge_l, cider, meteor, bleu = evaluate(test_loader, model, config, metriclogger, global_step)
