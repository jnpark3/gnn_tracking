from __future__ import annotations

import numpy as np
import torch
from torch import Tensor, nn
from torch_geometric.data import Data

from gnn_tracking.models.interaction_network import InteractionNetwork as IN
from gnn_tracking.models.mlp import MLP
from gnn_tracking.models.resin import ResIN


class EdgeClassifier(nn.Module):
    def __init__(
        self,
        node_indim,
        edge_indim,
        L=4,
        node_latentdim=8,
        edge_latentdim=12,
        r_hidden_size=32,
        o_hidden_size=32,
    ):
        super().__init__()
        self.node_encoder = MLP(node_indim, node_latentdim, 64, L=1)
        self.edge_encoder = MLP(edge_indim, edge_latentdim, 64, L=1)
        gnn_layers = []
        for _l in range(L):
            # fixme: Wrong parameters?
            gnn_layers.append(
                IN(
                    node_latentdim,
                    edge_latentdim,
                    node_outdim=node_latentdim,
                    edge_outdim=edge_latentdim,
                    edge_hidden_dim=r_hidden_size,
                    node_hidden_dim=o_hidden_size,
                )
            )
        self.gnn_layers = nn.ModuleList(gnn_layers)
        self.W = MLP(edge_latentdim, 1, 32, L=2)

    def forward(self, x: Tensor, edge_index: Tensor, edge_attr: Tensor) -> Tensor:
        node_latent = self.node_encoder(x)
        edge_latent = self.edge_encoder(edge_attr)
        for layer in self.gnn_layers:
            node_latent, edge_latent = layer(node_latent, edge_index, edge_latent)
        edge_weights = torch.sigmoid(self.W(edge_latent))
        return edge_weights


class ECForGraphTCN(nn.Module):
    def __init__(
        self,
        *,
        node_indim: int,
        edge_indim: int,
        interaction_node_dim: int = 5,
        interaction_edge_dim: int = 4,
        hidden_dim: int | float = None,
        L_ec: int = 3,
        alpha: float = 0.5,
        residual_type="skip1",
        use_intermediate_edge_embeddings: bool = True,
        use_node_embedding: bool = True,
        residual_kwargs: dict | None = None,
    ):
        """Edge classification step to be used for Graph Track Condensor network
        (Graph TCN)

        Args:
            node_indim: Node feature dim
            edge_indim: Edge feature dim
            interaction_node_dim: Node dimension for interaction networks.
                Defaults to 5 for backward compatibility, but this is probably
                not reasonable.
            interaction_edge_dim: Edge dimension of interaction networks
                Defaults to 4 for backward compatibility, but this is probably
                not reasonable.
            hidden_dim: width of hidden layers in all perceptrons (edge and node
                encoders, hidden dims for MLPs in object and relation networks). If
                None: choose as maximum of input/output dims for each MLP separately
            L_ec: message passing depth for edge classifier
            alpha: strength of residual connection for EC
            residual_type: type of residual connection for EC
            use_intermediate_edge_embeddings: If true, don't only feed the final
                encoding of the stacked interaction networks to the final MLP, but all
                intermediate encodings
            use_node_embedding: If true, feed node attributes to the final MLP for
                EC
            residual_kwargs: Keyword arguments passed to `ResIN`
        """
        super().__init__()
        if residual_kwargs is None:
            residual_kwargs = {}
        residual_kwargs["collect_hidden_edge_embeds"] = use_intermediate_edge_embeddings
        self.relu = nn.ReLU()

        self.ec_node_encoder = MLP(
            node_indim, interaction_node_dim, hidden_dim=hidden_dim, L=2, bias=False
        )
        self.ec_edge_encoder = MLP(
            edge_indim, interaction_edge_dim, hidden_dim=hidden_dim, L=2, bias=False
        )
        self.ec_resin = ResIN(
            node_dim=interaction_node_dim,
            edge_dim=interaction_edge_dim,
            object_hidden_dim=hidden_dim,
            relational_hidden_dim=hidden_dim,
            alpha=alpha,
            n_layers=L_ec,
            residual_type=residual_type,
            residual_kwargs=residual_kwargs,
        )

        w_input_dim = interaction_edge_dim
        if use_intermediate_edge_embeddings:
            w_input_dim = self.ec_resin.concat_edge_embeddings_length
        if use_node_embedding:
            w_input_dim += interaction_node_dim * 2
        self.W = MLP(input_size=w_input_dim, output_size=1, hidden_dim=hidden_dim, L=3)
        self._use_intermediate_edge_embeddings = use_intermediate_edge_embeddings
        self._use_node_embedding = use_node_embedding

    def forward(
        self,
        data: Data,
    ) -> dict[str, Tensor]:
        """Returns dictionary of the following:

        * ``W``: Edge weights
        * ``node_embedding``: Last node embedding (result of last interaction network)
        * ``edge_embedding``: Last edge embedding (result of last interaction network)
        """
        x, edge_index, edge_attr = data.x, data.edge_index, data.edge_attr
        h_ec = self.relu(self.ec_node_encoder(x))
        edge_attr_ec = self.relu(self.ec_edge_encoder(edge_attr))
        h_ec, edge_attr_ec, edge_attrs_ec = self.ec_resin(
            h_ec, edge_index, edge_attr_ec
        )

        w_input = edge_attr_ec
        if self._use_intermediate_edge_embeddings:
            w_input = torch.cat(edge_attrs_ec, dim=1)
        if self._use_node_embedding:
            h_ec_0 = h_ec[edge_index[0]]
            h_ec_1 = h_ec[edge_index[1]]
            w_input = torch.cat([h_ec_0, h_ec_1, w_input], dim=1)
        edge_weights = torch.sigmoid(self.W(w_input))
        return {
            "W": edge_weights.squeeze(),
            "node_embedding": h_ec,
            "edge_embedding": edge_attr_ec,
        }


class PerfectEdgeClassification(nn.Module):
    def __init__(self, tpr=1.0, tnr=1.0, false_below_pt=0.0):
        """An edge classifier that is perfect because it uses the truth information.
        If TPR or TNR is not 1.0, noise is added to the truth information.

        This can be used to evaluate the maximal possible performance of a model
        that relies on edge classification as a first step (e.g., the object
        condensation approach).

        Args:
            tpr: True positive rate
            tnr: False positive rate
            false_below_pt: If not 0.0, all true edges between hits corresponding to
                particles with a pt lower than this threshold are set to false.
                This is not counted towards the TPR/TNR but applied afterwards.
        """
        super().__init__()
        assert 0.0 <= tpr <= 1.0
        self.tpr = tpr
        assert 0.0 <= tnr <= 1.0
        self.tnr = tnr
        self.false_below_pt = false_below_pt

    def forward(self, data: Data) -> dict[str, Tensor]:
        r = data.y.bool()
        if not np.isclose(self.tpr, 1.0):
            true_mask = r.detach().clone()
            rand = torch.rand(int(true_mask.sum()), device=r.device)
            r[true_mask] = rand <= self.tpr
        if not np.isclose(self.tnr, 1.0):
            false_mask = (~r).detach().clone()
            rand = torch.rand(int(false_mask.sum()), device=r.device)
            r[false_mask] = ~(rand <= self.tnr)
        if self.false_below_pt > 0.0:
            false_mask = data.pt < self.false_below_pt
            r[false_mask] = False
        # Return as float, because that's what a normal model would do
        # (and also what BCE expects)
        return {"W": r.float()}
