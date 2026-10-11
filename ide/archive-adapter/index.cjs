// Theia expects the original CommonJS API. Use the maintained extractor.
module.exports = (...args) => import('@xhmikosr/decompress').then(module => module.default(...args));
