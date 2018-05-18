import math
import torch
import torch.cuda
import torchvision.transforms as transforms
import torch.utils.data as data
import torchvision.datasets as dsets
import os
from utils.BBBConvmodel import BBBCNN
from utils.BBBConvlayer import GaussianVariationalInference

cuda = torch.cuda.is_available()

'''
MODEL HYPERPARAMETERS
'''
save_model = True
is_training = True  # set to "False" for evaluation of network ability to remember previous tasks
pretrained = False  # change pretrained to "True" for continual learning
task_num = 10  # number of tasks, i.e. possible output classes
num_samples = 10  # because of Casper's trick
batch_size = 64
beta_type = "Blundell"
num_epochs = 100
p_logvar_init = 0
q_logvar_init = -10
lr = 1e-5
weight_decay = 0.0005


'''
LOADING DATASET
'''

#train_root = '/home/felix/PycharmProjects/MasterProject/data/train/{}'.format(task_num)
#train_root = '/home/felix/PycharmProjects/MasterProject/data/tiny-imagenet-200/train'
#val_root = '/home/felix/PycharmProjects/MasterProject/data/val/{}'.format(task_num)
#val_root = '/home/felix/PycharmProjects/MasterProject/data/tiny-imagenet-200/val'
"""
train_dataset = dsets.ImageFolder(root=train_root,
                                  transform=transforms.Compose([
                                      #transforms.RandomVerticalFlip(),
                                      transforms.Resize((227, 227)),
                                      transforms.ToTensor(),
                                      transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))]))

val_dataset = dsets.ImageFolder(root=val_root,
                                transform=transforms.Compose([
                                    transforms.Resize((227, 227)),
                                    transforms.ToTensor(),
                                    transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))]))

"""
transform = transforms.Compose([transforms.Resize((227, 227)), transforms.ToTensor(),
                                transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225))])

train_dataset = dsets.CIFAR10(root="data", download=True, transform=transform)
val_dataset = dsets.CIFAR10(root='data', download=True, train=False, transform=transform)

'''
MAKING DATASET ITERABLE
'''

print('length of training dataset:', len(train_dataset))
n_iterations = num_epochs * (len(train_dataset) / batch_size)
n_iterations = int(n_iterations)
print('Number of iterations: ', n_iterations)

loader_train = data.DataLoader(dataset=train_dataset, batch_size=batch_size, shuffle=True)

loader_val = data.DataLoader(dataset=val_dataset, batch_size=batch_size, shuffle=False)


# enable loading of weights to transfer learning
def cnnmodel(pretrained):
    model = BBBCNN(num_tasks=task_num)

    if pretrained:
        # load pretrained prior distribution of one class (e.g. cup)
        with open("/home/adllo/PycharmProjects/FelixMaster/results/....pkl", "rb") as previous:
            d = torch.load(previous)
            model.load_prior(d)

    return model


'''
INSTANTIATE MODEL
'''

model = cnnmodel(pretrained=pretrained)

if cuda:
    model.cuda()

'''
INSTANTIATE VARIATIONAL INFERENCE AND OPTIMISER
'''
vi = GaussianVariationalInference(torch.nn.CrossEntropyLoss())
optimiser = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=lr, weight_decay=weight_decay)

'''
check parameter matrix shapes
'''

# how many parameter matrices do we have?
print('Number of parameter matrices: ', len(list(model.parameters())))

for i in range(len(list(model.parameters()))):
    print(list(model.parameters())[i].size())

'''
TRAIN MODEL
'''

logfile = os.path.join('diagnostics.txt')
with open(logfile, "w") as fh:
    fh.write("")


def run_epoch(loader, epoch, is_training=False):
    m = math.ceil(len(loader.dataset) / loader.batch_size)

    accuracies = []
    likelihoods = []
    kls = []
    losses = []

    for i, (images, labels) in enumerate(loader):
        # Repeat samples (Casper's trick)
        x = images.view(batch_size, 3, 227, 227).repeat(num_samples, 1, 1, 1)
        y = labels.repeat(num_samples)

        if cuda:
            x = x.cuda()
            y = y.cuda()

        if beta_type == "Blundell":
            beta = 2 ** (m - (i + 1)) / (2 ** m - 1)
        elif beta_type == "Soenderby":
            beta = min(epoch / (num_epochs//4), 1)
        elif beta_type == "Standard":
            beta = 1 / m
        else:
            beta = 0

        logits, kl = model.probforward(x)
        loss = vi(logits, y, kl, beta)
        ll = -loss.data.mean() + beta*kl.data.mean()

        if is_training:
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()

        _, predicted = logits.max(1)
        print('predicted', predicted)
        accuracy = (predicted.data.cpu() == y.cpu()).float().mean()
        print('accuracy', accuracy)

        accuracies.append(accuracy)
        losses.append(loss.data.mean())
        kls.append(beta*kl.data.mean())
        likelihoods.append(ll)

    diagnostics = {'loss': sum(losses)/len(losses),
                   'acc': sum(accuracies)/len(accuracies),
                   'kl': sum(kls)/len(kls),
                   'likelihood': sum(likelihoods)/len(likelihoods)}

    return diagnostics


for epoch in range(num_epochs):
    if is_training is True:
        diagnostics_train = run_epoch(loader_train, epoch, is_training=True)
        diagnostics_val = run_epoch(loader_val, epoch)
        diagnostics_train = dict({"type": "train", "epoch": epoch}, **diagnostics_train)
        diagnostics_val = dict({"type": "validation", "epoch": epoch}, **diagnostics_val)
        print(diagnostics_train)
        print(diagnostics_val)
    else:
        diagnostics_val = run_epoch(loader_val, epoch)
        diagnostics_val = dict({"type": "validation", "epoch": epoch}, **diagnostics_val)
        print(diagnostics_val)


if save_model is True:
    torch.save(model.state_dict(), "weights.pkl")
