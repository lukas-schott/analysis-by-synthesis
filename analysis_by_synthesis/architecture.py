import torch
from torch import nn
from one_lip_ae.one_lip_nets import get_one_lip_model
from .loss_functions import samplewise_loss_function


def get_base_model(args):
    if args.base_model == 'one_lip_ae':
        base_model = get_one_lip_model(args)
    elif args.base_model == 'vae':
        base_model = VAE(n_latents=args.n_latents_per_class)
    else:
        raise NotImplementedError(f'model {args.base_model} is not implemented try OneLipAE or VAE')

    return base_model


class Encoder(nn.Module):
    def __init__(self, n_latents):
        super().__init__()

        self.shared = nn.Sequential(
            nn.Conv2d(1, 32, 5),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.Conv2d(32, 32, 4, stride=2),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.Conv2d(32, 64, 3, stride=2),
            nn.BatchNorm2d(64),
            nn.ELU(),
        )

        self.conv_mu = nn.Conv2d(64, n_latents, 5)
        self.conv_logvar = nn.Conv2d(64, n_latents, 5)

    def forward(self, x):
        shared = self.shared(x)
        mu = self.conv_mu(shared)
        logvar = self.conv_logvar(shared)
        return mu, logvar


class Decoder(nn.Module):
    def __init__(self, n_latents):
        super().__init__()

        self.layers = nn.Sequential(
            nn.ConvTranspose2d(n_latents, 32, 4),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.ConvTranspose2d(32, 16, 5, stride=2),
            nn.BatchNorm2d(16),
            nn.ELU(),
            nn.ConvTranspose2d(16, 16, 5, stride=2),
            nn.BatchNorm2d(16),
            nn.ELU(),
            nn.ConvTranspose2d(16, 1, 4),
        )

        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = self.layers(x)
        return self.sigmoid(x)


class ColorEncoder(nn.Module):
    def __init__(self, n_latents):
        super().__init__()

        self.shared = nn.Sequential(
            nn.Conv2d(3, 32, 5),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.Conv2d(32, 32, 4, stride=2),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.Conv2d(32, 32, 3),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.Conv2d(32, 64, 3, stride=2),
            nn.BatchNorm2d(64),
            nn.ELU(),
        )

        self.conv_mu = nn.Conv2d(64, n_latents, 5)
        self.conv_logvar = nn.Conv2d(64, n_latents, 5)

    def forward(self, x):
        shared = self.shared(x)
        mu = self.conv_mu(shared)
        logvar = self.conv_logvar(shared)
        return mu, logvar


class ColorDecoder(nn.Module):
    def __init__(self, n_latents):
        super().__init__()

        self.layers = nn.Sequential(
            nn.ConvTranspose2d(n_latents, 32, 4),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.ConvTranspose2d(32, 32, 5, stride=2),
            nn.BatchNorm2d(32),
            nn.ELU(),
            nn.ConvTranspose2d(32, 16, 3),
            nn.BatchNorm2d(16),
            nn.ELU(),
            nn.ConvTranspose2d(16, 16, 5, stride=2),
            nn.BatchNorm2d(16),
            nn.ELU(),
            nn.ConvTranspose2d(16, 3, 4),
        )

        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = self.layers(x)
        return self.sigmoid(x)


class VAE(nn.Module):
    def __init__(self, n_latents, color=False):
        super().__init__()

        self.n_latents = n_latents
        if color:
            self.encoder = ColorEncoder(self.n_latents)
            self.decoder = ColorDecoder(self.n_latents)
        else:
            self.encoder = Encoder(self.n_latents)
            self.decoder = Decoder(self.n_latents)

    def reparameterize(self, mu, logvar):
        if self.training:
            std = torch.exp(0.5 * logvar)
            eps = torch.randn_like(std)
            return eps.mul(std).add_(mu)
        else:
            return mu

    def forward(self, x):
        mu, logvar = self.encoder(x)
        z = self.reparameterize(mu, logvar)
        return self.decoder(z), mu, logvar


class ABS(nn.Module):
    """ABS model implementation that performs variational inference
    and can be used for training."""

    def __init__(self, n_classes, beta, color=False, logit_scale=1.,
                 base_model_f=VAE, loss_f='vae'):
        super().__init__()

        self.beta = beta
        self.loss_f = loss_f
        self.base_models = nn.ModuleList([base_model_f() for _ in range(n_classes)])
        # self.logit_scale = nn.Parameter(torch.tensor(logit_scale))
        
        self.encoder_parameters = [item for vae in self.base_models for item in list(vae.encoder.parameters())]
        self.decoder_parameters = [item for vae in self.base_models for item in list(vae.decoder.parameters())]

    def forward(self, x):
        outputs = [base_model(x) for base_model in self.base_models]
        recs, mus, logvars = zip(*outputs)
        recs, mus, logvars = torch.stack(recs), torch.stack(mus), torch.stack(logvars)
        losses = [samplewise_loss_function(x, recs.detach(), mus.detach(), logvars.detach(), self.beta,
                                           loss_f=self.loss_f)
                  for recs, mus, logvars in outputs]
        losses = torch.stack(losses)
        assert losses.dim() == 2
        logits = -losses.transpose(0, 1)
        # logits = logits * self.logit_scale
        return logits, recs, mus, logvars
