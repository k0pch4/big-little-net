import torch.nn as nn
from .resblock import *
import math
import torch.utils.model_zoo as model_zoo


__all__ = ['bL_ResNet', 'bl_resnet50', 'bl_resnet101', 'bl_resnet152']


model_urls = {
    # 'bl_resnet18': 'https://download.pytorch.org/models/resnet18-5c106cde.pth',
    # 'bl_resnet34': 'https://download.pytorch.org/models/resnet34-333f7ec4.pth',
    # 'bl_resnet50': 'https://download.pytorch.org/models/resnet50-19c8e357.pth',
    # 'bl_resnet101': 'https://download.pytorch.org/models/resnet101-5d3b4d8f.pth',
    # 'bl_resnet152': 'https://download.pytorch.org/models/resnet152-b121ed2d.pth',
}


class bL_ResNet(nn.Module):

    def __init__(self,
                 layers,
                 alpha = 2,
                 beta = 4,
                 num_classes=1000,
                 zero_init_residual=False):
        super().__init__()
        # pass 1 | Convolution
        self.inplanesB = 64
        self.inplanesL = 64
        self.conv1 = nn.Conv2d(3, 64, kernel_size=7,
                               stride=2, padding=3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)

        # pass 2 | bL-module
        self.conv2 = conv3x3(64, 64, stride=2)
        self.bn2 = nn.BatchNorm2d(64)
        self.littleblock = ResBlock(
            inplanes=64,
            planes=32, 
            stride=2, 
            expansion=2)

        # pass 3 | `ResBlockB`s & `ResBlockL`s
        arg_d = {
            'planes': 64,
            'beta': beta,
            'alpha': alpha,
            'reps': layers[0],
            'stride': 2,
            'expansion': 4
        }

        self.big_layer1 = self._make_layer(ResBlockB, arg_d)
        self.little_layer1 = self._make_layer(ResBlockL, arg_d)
        arg_d['stride'] = 2
        self.transition1 = self._make_layer(TransitionLayer, arg_d)

        arg_d['planes'] = 128; arg_d['reps'] = layers[1];
        self.big_layer2 = self._make_layer(ResBlockB, arg_d)
        self.little_layer2 = self._make_layer(ResBlockL, arg_d)
        arg_d['stride'] = 2
        self.transition2 = self._make_layer(TransitionLayer, arg_d)

        arg_d['planes'] = 256; arg_d['reps'] = layers[2];
        self.big_layer3 = self._make_layer(ResBlockB, arg_d)
        self.little_layer3 = self._make_layer(ResBlockL, arg_d)
        arg_d['stride'] = 1
        self.transition3 = self._make_layer(TransitionLayer, arg_d)

        arg_d['planes'] = 512; arg_d['reps'] = layers[3];
        arg_d['stride'] = 2
        self.res_layer1 = self._make_layer(ResBlock, arg_d)

        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(512 * arg_d['expansion'], num_classes)
        # training code takes care of taking the softmax vai logsofmax error

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)


    def _make_layer(self, Block, arg_d):
        '''Instantiates a sequence of `Block`s.

        :attr:`Block` is the Big-Little Net `Block` we have chosen
        :attr:`arg_d` are the arguments required to create objects for `Block`s
        '''
        # according to the Block, setting some defaults.
        if Block == ResBlockB:
            inplanes = self.inplanesB
        elif Block == ResBlockL:
            inplanes = self.inplanesL
            arg_d['planes'] = int(arg_d['planes'] / arg_d['alpha'])
            arg_d['reps'] = math.ceil(arg_d['reps'] / arg_d['beta'])
            arg_d['stride'] = 1 # stride is always 1 for ResBlockL
        elif Block == ResBlock:
            inplanes = self.inplanesB
            assert (inplanes == self.inplanesL) # debugging
        elif Block == TransitionLayer:
            inplanes = self.inplanesB
            assert (inplanes == self.inplanesL) # debugging
            arg_d['reps'] = 1 # reps is always one for TransitionLayer

        expansion = arg_d['expansion']
        stride = arg_d['stride']
        planes = arg_d['planes']
        reps = arg_d['reps']
        alpha = arg_d['alpha']
        beta = arg_d['beta']

        layers = []
        layers.append(Block(inplanes = inplanes, **arg_d))
        inplanes = self._new_inplanes(Block, planes, expansion, alpha)
        for _ in range(1, reps):
            layers.append(Block(inplanes = inplanes, **arg_d))
            inplanes = self._new_inplanes(Block, planes, expansion, alpha)

        # updating the current branch's inplanes
        if Block == ResBlockB:
            self.inplanesB = inplanes
        elif Block == ResBlockL:
            self.inplanesL = inplanes
            assert(self.inplanesB == inplanes) # should be equal
        elif Block in [ResBlock, TransitionLayer]:
            self.inplanesB = inplanes
            self.inplanesL = inplanes

        return nn.Sequential(*layers)

    def _new_inplanes(self, Block, planes, expansion, alpha):
        if Block in [ResBlockB, ResBlock, TransitionLayer]:
            new_inplanes = planes * expansion
        elif Block == ResBlockL:
            new_inplanes = int(planes * expansion * alpha)
        return new_inplanes


    def forward(self, x):
        # Conv
        main = self.conv1(x)
        main = self.bn1(main)
        main = self.relu(main)

        # pass 2 | bL-module
        little = main
        main = self.conv2(main)
        main = self.bn2(main)
        main = self.relu(main)
        little = self.littleblock(little)
        assert (main.shape == little.shape)
        main += little

        # pass 3 | `ResBlockB`s & `ResBlockL`s  planes = 64
        little = main
        main = self.big_layer1(main)
        little = self.little_layer1(little)
        print ('1st layer passed')
        main = self.transition1([main, little])

        # pass 4 | planes = 128
        little = main
        main = self.big_layer2(main)
        little = self.little_layer2(little)
        print ('2nd layer passed')
        main = self.transition2([main, little])

        # pass 5 | planes = 256
        little = main
        main = self.big_layer3(main)
        little = self.little_layer3(little)
        print ('3rd layer passed')
        main = self.transition3([main, little])

        # pass 6 | Res_Block | planes = 512
        main = self.res_layer1(main)

        # avg pooling
        main = self.avgpool(main)
        main = main.view(main.size(0), -1)
        main = self.fc(main)

        return main


def bl_resnet50(pretrained=False, **kwargs):
    """Constructs a bL-ResNet-50 model.

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = bL_ResNet([2, 3, 5, 1], **kwargs)
    # print ('model created')
    if pretrained:
        model.load_state_dict(model_zoo.load_url(model_urls['resnet50']))
    return model


def bl_resnet101(pretrained=False, **kwargs):
    """Constructs a bL-ResNet-101 model.

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = bL_ResNet([3, 7, 17, 3], **kwargs)
    if pretrained:
        model.load_state_dict(model_zoo.load_url(model_urls['resnet101']))
    return model


def bl_resnet152(pretrained=False, **kwargs):
    """Constructs a bL-ResNet-152 model.

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = bL_ResNet([4, 11, 29, 3], **kwargs)
    if pretrained:
        model.load_state_dict(model_zoo.load_url(model_urls['resnet152']))
    return model