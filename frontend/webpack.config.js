//
// This is written from the perspective of being run in the `js` container.
//
const OptimizeCssAssetsPlugin = require('optimize-css-assets-webpack-plugin');
const { CleanWebpackPlugin } = require('clean-webpack-plugin');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const HtmlWebpackPlugin = require('html-webpack-plugin');
const merge = require('webpack-merge');
const webpack = require('webpack');
const path = require('path');

const paths = {
  src: path.resolve(__dirname, 'src'),
  build: '/build'
}

const htmlConfig = {
  template: path.join(paths.src, 'index.html'),
  minify: {
    collapseWhitespace: true,
  }
}

const common = {
  entry: path.join(paths.src, 'index.js'),
  resolve: {
    extensions: ['.js', '.jsx', '.ts', '.tsx'],
    alias: {
      "react": "preact/compat",
      "react-dom": "preact/compat"
    },
  },
  output: {
    path: paths.build,
    filename: 'bundle-dev.[hash].js',
    // This publicPath is important and shared with Django configuration.
    publicPath: '/static/',
  },
  performance: {
    hints: false,
  },
  mode: 'development',
  module: {
    rules: [
      {
        test: /\.(js|jsx)$/,
        exclude: /(node_modules)/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: ['@babel/env']
          }
        }
      },
      {
        test: /\.(ts)$/,
        exclude: /(node_modules)/,
        use: {
          loader: 'awesome-typescript-loader',
          options: {
            useCache: false,
          }
        }
      },
      {
        test: /\.(css)$/,
        use: [
          'style-loader',
          { loader: 'css-loader', options: { importLoaders: 1 } },
          {
            loader: 'postcss-loader',
            options: {
              map: true, plugins: [
                require('postcss-import'),
                require('postcss-nested'),
                require('cssnano')
              ]
            }
          }
        ],
      },
      {
        test: /\.(png|jpg|gif)$/,
        type: 'asset/resource',
      },
      {
        test: /\.(woff|woff2|eot|ttf|otf)$/i,
        type: 'asset/resource',
      },
    ]
  },
  plugins: [
    new CleanWebpackPlugin(),
    new HtmlWebpackPlugin(htmlConfig),
    new MiniCssExtractPlugin({
      filename: '[name].css',
      chunkFilename: '[id].css',
      ignoreOrder: false,
    }),
  ]
};

const devSettings = {
  devtool: 'eval-source-map',
  devServer: {
    historyApiFallback: true,
    quiet: false,
    contentBase: paths.build,
  },
  plugins: [
    new CleanWebpackPlugin({ cleanStaleWebpackAssets: false }),
  ]
}

const prodSettings = {
  mode: 'production',
  optimization: {
    minimize: true,
  },
  devtool: false,
  output: {
    filename: 'bundle-prod.[hash].js',
  },
  plugins: [
    new webpack.DefinePlugin({ 'process.env': {
      NODE_ENV: JSON.stringify('production')
    }}),
    new OptimizeCssAssetsPlugin(),
  ]
}

const TARGET = process.env.npm_lifecycle_event;
process.env.BABEL_ENV = TARGET;

if (TARGET === 'start') {
  module.exports = merge(common, devSettings)
}

if (TARGET === 'build' || !TARGET) {
  module.exports = merge(common, prodSettings)
}
