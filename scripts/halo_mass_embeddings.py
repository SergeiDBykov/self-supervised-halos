import pandas as pd
import torch
import torchvision
import numpy as np
from tqdm import tqdm
import time
import os

import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as transforms
import torchvision.transforms.functional as TF
import matplotlib.pyplot as plt


from self_supervised_halos.scripts.base_model import BaseModel



def mask_time_series_batch(batch, mask_size=20, num_masks=2, num_masks_var=1):
    """
    Masks random subsequences of time series data in the batch.
    
    Parameters:
    batch (torch.Tensor): Batch of time series data of shape (batch_size, 100).
    mask_size (int): Size of the subsequences to mask.
    num_masks (int): Number of subsequences to mask.
    num_masks_var (int): Variance in the number of subsequences to mask.
    
    Returns:
    unmasked_signal (torch.Tensor): Original time series data.
    masked_signal (torch.Tensor): Time series data with masked values.
    prediction_mask (torch.Tensor): Mask indicating which tokens need to be predicted.
    """
    batch_size, seq_len = batch.size()
    
    unmasked_signal = batch.clone()
    masked_signal = batch.clone()
    prediction_mask = torch.zeros(batch_size, seq_len, dtype=torch.bool)

    for i in range(batch_size):
        available_indices = torch.where(~torch.isnan(batch[i]))[0]
        num_masks_actual = num_masks + np.random.randint(-num_masks_var, num_masks_var + 1)
        
        for _ in range(num_masks_actual):
            if len(available_indices) < mask_size*num_masks_actual:
                #print('Warning: Not enough available indices to mask. Skipping.')
                break
            start_idx = np.random.choice(available_indices[:-mask_size + 1])
            mask_indices = torch.arange(start_idx, start_idx + mask_size)
            
            mask_indices = mask_indices[mask_indices < seq_len]
            mask_indices = mask_indices[torch.isin(mask_indices, available_indices)]
            
            masked_signal[i, mask_indices] = float('nan')
            prediction_mask[i, mask_indices] = True

        
    return unmasked_signal, masked_signal, prediction_mask




class HaloMassHistTransformer(nn.Module):
    def __init__(self, embed_dim=16, num_heads=4, num_layers=3, output_dim=1, dim_feedforward = 32, dropout=0.1):
        super(HaloMassHistTransformer, self).__init__()
        self.conv1d = nn.Conv1d(in_channels=1, out_channels=embed_dim, kernel_size=1)

        encoder_layer = nn.TransformerEncoderLayer(d_model=embed_dim, nhead=num_heads,
                                                   dim_feedforward=dim_feedforward,
                                                   dropout = dropout,
                                                   batch_first = False)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        self.fc_out = nn.Linear(embed_dim, output_dim)

    def forward(self, x, src_key_padding_mask=None):
        # x shape: (batch_size, seq_len)
        x = x.unsqueeze(1)  # add channel dimension: (batch_size, 1, seq_len)
        x = self.conv1d(x)  # apply Conv1d: (batch_size, embed_dim, seq_len)
        x = x.permute(2, 0, 1)  # (seq_len, batch_size, embed_dim)
        x = self.transformer(x, src_key_padding_mask=src_key_padding_mask)
        x = x.permute(1, 0, 2)  # (batch_size, seq_len, embed_dim)
        x = self.fc_out(x)  # (batch_size, seq_len, output_dim)
        return x




class RegressionModel(BaseModel):
    def __init__(self, 
                optimizer_class=torch.optim.Adam,
                optimizer_params={}, 
                scheduler_class=torch.optim.lr_scheduler.StepLR,
                scheduler_params={},
                criterion=None, 
                history=None,
                transform = mask_time_series_batch,
                 ):
        model = HaloMassHistTransformer()
        super().__init__(model, 
                        optimizer_class = optimizer_class
                        optimizer_params=optimizer_params,
                        scheduler_class = scheduler
                        scheduler_params=scheduler_params)
        self.criterion = criterion
        self.history = history if history else {'train_loss': [], 'val_loss': [], 'learning_rate': []}

    def forward(self, x):
        return self.model(x)
    
    def training_step(self, batch, device, verbose = False):
        inputs, targets = batch
        time_series = inputs[0][2].to(device)
        time_series = time_series.float()


        # Mask some subsequences in the batch
        unmasked_signal, masked_signal, prediction_mask = self.transform(time_series)

        # Replace NaNs with zeros for processing
        masked_signal_filled = torch.nan_to_num(masked_signal, nan=-10.0)

        # Create a src_key_padding_mask for the transformer
        src_key_padding_mask = torch.isnan(masked_signal)

        # Forward pass
        predictions = self.model(masked_signal_filled, src_key_padding_mask=src_key_padding_mask)
        predictions = predictions.squeeze(-1) # remove last dimension

        loss = self.criterion(predictions[prediction_mask], unmasked_signal[prediction_mask])

        if verbose:
            print(f"Loss: {loss.item()}")

        return loss


    def show_transforms(self, dataloader, device):
        self.model.eval()
        with torch.no_grad():
            for batch in tqdm(dataloader, desc="Trial Forward Pass"):
                inputs, targets = batch
                time_series = inputs[0][2].to(device)
                time_series = time_series.float()
                if self.transform:
                    unmasked_signal, masked_signal, prediction_mask = self.transform(inputs)
                else:
                    raise NotImplementedError("Transform not implemented")
                        
                masked_signal_filled = torch.nan_to_num(masked_signal, nan=-10.0)

                src_key_padding_mask = torch.isnan(masked_signal)

                predictions = self.model(masked_signal_filled, src_key_padding_mask=src_key_padding_mask)
                
                
                n_img_to_plots = 3
                fig, ax = plt.subplots(1, n_img_to_plots, figsize=(10, 10))
                for i in range(n_img_to_plots):
                    #plot masked input
                    input_masked = masked_signal[i].cpu().numpy()
                    predictions = predictions[i].cpu().numpy()
                    input_all = time_series[i].cpu().numpy()

                    ax[i].plot(input_masked,  'r--', lw = 1, alpha = 0.5, label='Masked Input')
                    ax[i].plot(predictions,  'g-', lw = 1, alpha = 0.5, label='Predictions')
                    ax[i].plot(input_all, 'k:', lw = 1, alpha = 0.5,label='All')

                plt.show()
                return inputs
