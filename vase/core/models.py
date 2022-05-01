# AUTOGENERATED! DO NOT EDIT! File to edit: 02a_core.models.ipynb (unless otherwise specified).

__all__ = ['Encoder', 'FCEncoder', 'EnvironmentInference', 'env_dist_to_idx', 'Decoder', 'FCDecoder', 'reparam',
           'VanillaVAE', 'PaperVanillaVAE', 'FCVAE', 'latent_mask', 'apply_mask', 'LatentMaskVAE', 'EnvInferVAE',
           'generate_samples', 'GenReplayVAE']

# Cell
import torch
from torch import nn
from torch.nn import functional as F
import numpy as np
import copy

from ..config import DATA_PATH
from .utils import rec_likelihood, kl_div_stdnorm, disable_gradient, enable_gradient

# Cell
class Encoder(nn.Module):
    def __init__(self, latents=10):
        super().__init__()
        self.latents = latents
        #NOTE: no pooling? should compare results with and without
        self.conv1 = nn.Conv2d(1, 64, (4,4), stride=2, padding=1)
        self.conv2 = nn.Conv2d(64, 64, (4,4), 2, padding=1)
        self.conv3 = nn.Conv2d(64, 128, (4,4), 2, padding=1)
        self.conv4 = nn.Conv2d(128, 128, (4,4), 2, padding=1)
        self.linear = nn.Linear(2048, 256)
        self.linear_mu = nn.Linear(256, self.latents)
        self.linear_logvar = nn.Linear(256, self.latents)
        self.relu = nn.ReLU()

    def forward(self, x):
        """
        Returns mean and standard deviation to parameterize sigmoid,
        and final layer to compute environment
        """
        x = self.relu(self.conv1(x)) # (batch_size, 64, 32, 32)
        x = self.relu(self.conv2(x)) # (batch_size, 64, 16, 16)
        x = self.relu(self.conv3(x)) # (batch_size, 128, 8, 8)
        x = self.relu(self.conv4(x)) # (batch_size, 128, 4, 4)
        x = x.reshape(-1, 2048)
        final = self.relu(self.linear(x))
        mu = self.linear_mu(final)
        logvar = self.linear_logvar(final)
        return mu, logvar, final.detach() #detach to prevent gradient flow

# Cell
class FCEncoder(nn.Module):
    def __init__(self, latents: int):
        super().__init__()
        self.latents = latents
        self.latents = latents
        self.linear1 = nn.Linear(784, 50)
        self.linear_mu = nn.Linear(50, latents)
        self.linear_logvar = nn.Linear(50, latents)
        self.act = nn.ReLU()

    def forward(self, x):
        x = x.reshape(-1, 784)
        final = self.act(self.linear1(x))
        mu = self.linear_mu(final)
        logvar = self.linear_logvar(final) #TODO: should this be exponentiated?
        return mu, logvar, final

# Cell
class EnvironmentInference(nn.Module):
    def __init__(self, max_environmnets: int, input_dim:int):
        super().__init__()
        self.max_environments = max_environmnets
        self.input_dim = input_dim
        self.linear = nn.Linear(input_dim, max_environmnets)
        self.softmax = nn.Softmax(dim=1)

    def forward(self, final_latent):
        x = self.linear(final_latent)
        return self.softmax(x)

# Cell
def env_dist_to_idx(env_dist: torch.Tensor, max_environments: int) -> torch.Tensor:
    """Converts a batch of distributions to a one-hot vector"""
    batch_size = env_dist.shape[0]
    avg_env_dist = env_dist.mean(dim=0)
    env_idx = torch.argmax(avg_env_dist)
    return torch.ones((batch_size), dtype=torch.int64) * env_idx


# Cell
class Decoder(nn.Module):
    def __init__(self, latents:int, max_envs=0):
        super().__init__()
        self.max_envs = max_envs
        self.latents = latents
        self.linear2 = nn.Linear(latents + max_envs, 256)
        self.linear1 = nn.Linear(256, 2048)
        self.conv4 = nn.ConvTranspose2d(128, 128, (4,4), 2, padding=1)
        self.conv3 = nn.ConvTranspose2d(128, 64, (4,4), 2, padding=1)
        self.conv2 = nn.ConvTranspose2d(64, 64, (4,4), 2, padding=1)
        self.conv1 = nn.ConvTranspose2d(64, 1, (4,4), 2, padding=1)
        self.relu = nn.ReLU()
        self.sigmoid = nn.Sigmoid()

    def forward(self, z, s=None):
        """
        Decode the latent and environmental variables

        Args:
            z (Tensor): latent variables
            s (Tensor): environment indicies (not one hot)

        Returns:
            Means for (batchsize, widgt, height) Bernoulli's (which can be interpreted as the reconstructed image)
        """

        if s is not None:
            s_one_hot = F.one_hot(s, num_classes=self.max_envs)
            z = torch.cat((z, s_one_hot), dim=1)
        x = self.relu(self.linear2(z)) # (batch_size, 256)
        x = self.relu(self.linear1(x)) # (batch_size, 512)
        x = x.reshape(-1, 128, 4, 4) # (batch_size, 128, 2, 2)
        x = self.relu(self.conv4(x)) # (batch_size, 128, 6, 6)
        x = self.relu(self.conv3(x)) # (batch_size, 64, 14, 14)
        x = self.relu(self.conv2(x)) # (batch_size, 64, 30, 30) WRONG (should be 31)
        out = self.sigmoid(self.conv1(x))
        return out

# Cell
class FCDecoder(nn.Module):
    def __init__(self, latents: int, max_envs=0):
        super().__init__()
        self.max_envs = max_envs
        self.latents = latents
        self.linear1 = nn.Linear(latents + max_envs, 50)
        self.linear2 = nn.Linear(50, 784)
        self.relu = nn.ReLU()
        self.sigmoid = nn.Sigmoid()

    def forward(self, z, s=None):
        """
        Decode the latent and environmental variables

        Args:
            z (Tensor): latent variables
            s (Tensor): one-hot encoded environmental variable (not sure how this works...)

        Returns:
            Means for (batchsize, widgt, height) Bernoulli's (which can be interpreted as the reconstructed image)
        """
        if s is not None:
            s_one_hot = F.one_hot(s, num_classes=self.max_envs)
            z = torch.cat((z, s_one_hot), dim=1)
        x = self.relu(self.linear1(z))
        x = self.linear2(x)
        out = self.sigmoid(x)
        out = out.reshape(-1, 1, 28, 28)
        return out

# Cell
def reparam(mu, logvar, device='cpu'):
    eps = torch.randn(logvar.shape).to(device)
    std = (0.5 * logvar).exp()
    return mu + std * eps

# Cell
class VanillaVAE(nn.Module):
    def __init__(self, encoder: type, decoder: type, latents: int, device: str):
        super().__init__()
        self.encoder = encoder(latents=latents)
        self.decoder = decoder(latents=latents)
        self.device = device

    def forward(self, x):
        mu, logvar, _final = self.encoder(x)
        if self.training:
            z = reparam(mu, logvar, device=self.device)
        else:
            z = mu
        rec_img = self.decoder(z=z)
        return rec_img, mu, logvar

# Cell
class PaperVanillaVAE(VanillaVAE):
    def __init__(self, latents: int, device:str):
        super().__init__(encoder=Encoder, decoder=Decoder, latents=latents, device=device)

# Cell
class FCVAE(VanillaVAE):
    def __init__(self, latents: int, device='cpu'):
        super().__init__(encoder=FCEncoder, decoder=FCDecoder, latents=latents, device=device)


# Cell
def latent_mask(z, lam, lam_1=1e-4):
    std, mean = torch.std_mean(z, dim=0)
    std = std[:,None]
    mean = mean[:, None]
    logvar = torch.log(std.pow(2))
    alphas = kl_div_stdnorm(mean, logvar)
    alphas[alphas < lam_1] = 0
    #alphas[alphas > lam_2] = 1
    a = alphas < lam
    return a

# Cell
def apply_mask(a, z, mu, logvar):
    std_norm = torch.randn(z.shape)
    z = a * z + (~a) * std_norm # "reparam" trick again
    mu[:,~a] = 0
    logvar[:,~a] = 0
    return z, mu, logvar

# Cell
class LatentMaskVAE(VanillaVAE):
    def __init__(self, encoder: type, decoder: type, latents: int, device: str, lam: float):
        self.lam = lam
        super().__init__(encoder, decoder, latents, device)

    def forward(self, x):
        mu, logvar, _final = self.encoder(x)
        if self.training:
            z = reparam(mu, logvar, device=self.device)
        else:
            z = mu

        #latent masking
        a = latent_mask(z, self.lam)
        z, mu, logvar = apply_mask(a, z, mu, logvar)

        rec_img = self.decoder(z=z)
        return rec_img, mu, logvar

# Cell
class EnvInferVAE(nn.Module):
    def __init__(self, encoder: type, decoder: type, latents: int, max_envs: int, lam: float, kappa: float, device: str,
        Tau: float=2, used_epochs: int=100, used_lr: float=1e-2, used_delta: float=.95):
        super().__init__()
        self.latents = latents
        self.m = 0
        self.max_envs = max_envs
        self.lam = lam
        self.kappa = kappa
        self.Tau = Tau
        self.used_epochs = used_epochs
        self.used_lr = used_lr
        self.used_delta = used_delta
        self.env_count = [0] * self.max_envs
        self.rec_loss_avgs = []
        self.latent_masks = []
        self.used_masks = []
        self.used_masks = []
        self.encoder = encoder(latents=latents)
        self.decoder = decoder(latents=latents, max_envs=max_envs)
        self.device = device

    def forward(self, x):
        batch_size = x.shape[0]
        mu, logvar, _final = self.encoder(x)
        if self.training:
            z = reparam(mu, logvar, device=self.device)
        else:
            z = mu

        #latent masking
        a = latent_mask(z, self.lam)
        z, mu, logvar = apply_mask(a, z, mu, logvar)

        #infer environment
        env_idx = self.infer_env(x, z, a)
        s = torch.ones(batch_size, dtype=torch.int64) * env_idx

        rec_img = self.decoder(z=z, s=s)
        return rec_img, mu, logvar, env_idx, z

    def infer_env(self, x, z, a):
        # u = model.used(z)
        batch_size = x.shape[0]

        #get maximum likelihood environment using "analysis by synthesis"
        losses = []
        for s_i in range(self.m+1):
            s = torch.ones(batch_size, dtype=torch.int64) * s_i
            with torch.no_grad():
                x_rec = self.decoder(z, s)
                losses.append(torch.sum(rec_likelihood(x, x_rec)))
        env_idx = torch.argmin(torch.tensor(losses))

        #get used mask
        u = self.used_latents(x, z, env_idx, batch_size, epochs=self.used_epochs, lr=self.used_lr, Tau=self.Tau, delta=self.used_delta)
        if u.sum() == 0:
            print("some latent(s) are not used!")

        rec_loss = losses[env_idx]
        avg_rec_loss = rec_loss / batch_size

        if self.env_count[0] == 0:
            self.init_env(batch_size, a, u, avg_rec_loss)
            return self.m
        elif avg_rec_loss > self.kappa * self.rec_loss_avgs[env_idx] and self.m < self.max_envs-1:
            print("New environment: anomolous reconstruction loss")
            self.m +=1
            self.init_env(batch_size, a, u, avg_rec_loss)
            return self.m
        elif not torch.equal(a * self.used_masks[env_idx], self.latent_masks[env_idx] * u) and self.m < self.max_envs-1:
            print("New environment: latent masks did not match")
            self.m +=1
            self.init_env(batch_size, a, u, avg_rec_loss)
            return self.m
        else:
            #TODO add warning about exceeding max envs or something
            self.env_count[env_idx] += batch_size
            self.rec_loss_avgs[env_idx] = rec_loss #cumulative average
            return env_idx

    def used_latents(self, batch, z, env_idx, batch_size, epochs, lr, Tau, delta):
        sigma = torch.zeros([self.latents]) #TODO: could change init scheme
        sigma.requires_grad_(True)
        decoder_copy = copy.deepcopy(self.decoder)
        disable_gradient(decoder_copy)
        optimizer = torch.optim.SGD(params=[sigma], lr=lr)
        s = torch.ones(batch_size, dtype=torch.int64) * env_idx
        for i in range(epochs):
            optimizer.zero_grad()
            eps = torch.randn(sigma.shape[0]) * sigma
            z_e = (1-delta) * z + (delta + eps)
            rec_batch = self.decoder(z_e, s)
            rec_loss = torch.mean(rec_likelihood(batch, rec_batch))
            sum_sigma = torch.sum(sigma)
            loss = rec_loss - sum_sigma
            loss.backward(retain_graph=True)
            optimizer.step()
            if i % 10 == 0:
                pass
                #print(sigma)
                #print(loss)
                #print(sigma.sum())
        return sigma < Tau

    def init_env(self, batch_size, a, u, avg_rec_loss):
        self.env_count[self.m] += batch_size
        self.latent_masks.append(a)
        self.used_masks.append(u)
        self.rec_loss_avgs.append(avg_rec_loss)



# Cell
def generate_samples(vae: EnvInferVAE, batch_size: int):
    z = torch.randn(size=(batch_size, vae.latents))
    s = torch.randint(0, vae.m+1, (batch_size,))
    x_sample = vae.decoder(z, s)
    return x_sample

# Cell
class GenReplayVAE(EnvInferVAE):
    def __init__(self, encoder: type, decoder: type, latents: int, max_envs: int, lam: float, kappa: float, device: str,
        Tau: float=2, used_epochs: int=100, used_lr: float=1e-2, used_delta: float=.95):
        self.encoder_type = encoder
        self.decoder_type = decoder
        super().__init__(encoder, decoder, latents, max_envs, lam, kappa, device, Tau, used_epochs, used_lr, used_delta)
        self.old_model = []

    def sample(self, batch_size, increment=True):
        if self.m == 0:
            raise Exception("should not generate samples on current environment")
        samples = generate_samples(self.old_model[0], batch_size)
        return samples

    def forward_halu(self, x):
        rec_X, _mu, _logvar, _env_idx, z = self(x)
        old_rec_X, _old_mu, _old_logvar, _old_env_idx, old_z = self.old_model[0](x)
        return rec_X, old_rec_X, z, old_z

    def init_env(self, batch_size, a, u, avg_rec_loss):
        if self.m > 0: #save current state of model for experience replay
            old_model = self.copy_self(self.m-1)
            disable_gradient(old_model)
            self.old_model = [old_model]
        super().init_env(batch_size, a, u, avg_rec_loss)

    def copy_self(self, m):
        copy = EnvInferVAE(self.encoder_type, self.decoder_type, self.latents, self.max_envs, self.lam, self.kappa, self.device)
        copy.load_state_dict(self.state_dict())
        copy.m = m
        return copy