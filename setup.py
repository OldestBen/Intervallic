from setuptools import setup, find_packages

setup(
    name="intervallic",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "plexapi>=4.15.0",
        "mutagen>=1.47.0",
        "pyyaml>=6.0",
        "click>=8.1.0",
        "paramiko>=3.4.0",
        "qrcode>=7.4.2",
    ],
    entry_points={
        "console_scripts": [
            "intervallic=intervallic.cli:main",
        ],
    },
    python_requires=">=3.9",
)
